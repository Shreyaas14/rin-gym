"""Protected holdout and prospective logging commands."""
import json
from pathlib import Path


def add_commands(groups):
    parser = groups.add_parser('eval', help='Free local eval quality, protected holdout and prospective logging')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='Initialize a pending holdout policy; does not select games')
    commands.add_parser('status', help='Check ledger integrity and show enrollment/coverage counts')
    register = commands.add_parser('register', help='Freeze candidate artifacts before cohort enrollment')
    register.add_argument('--name', required=True)
    register.add_argument('--artifact', type=Path, action='append', required=True)
    enroll = commands.add_parser('enroll', help='Freeze future fixtures, cases and the primary comparison')
    enroll.add_argument('--cohort', type=Path, required=True)
    record = commands.add_parser('record', help='Record a prediction receipt now; no caller-provided issue time')
    record.add_argument('--candidate', required=True)
    record.add_argument('--case', required=True)
    record.add_argument('--input', type=Path, required=True, help='Strict observation envelope JSON')
    result = record.add_mutually_exclusive_group(required=True)
    result.add_argument('--probabilities', type=float, nargs=3, metavar=('HOME', 'DRAW', 'AWAY'))
    result.add_argument('--error', help='Record a failed attempt, such as a model timeout')
    resolve = commands.add_parser('resolve', help='Append an adjudicated regulation outcome after the deadline')
    resolve.add_argument('--match', required=True)
    resolve.add_argument('--target', type=int, choices=[0, 1, 2], required=True)
    resolve.add_argument('--ended-utc', required=True)
    resolve.add_argument('--source', type=Path, required=True, help='Adjudication evidence file; SHA-256 is recorded')
    commands.add_parser('score', help='Release the frozen report only after every match resolves; subsequent calls read it')
    commands.add_parser('export-mlflow', help='Export the already-released holdout report to local MLflow')
    check = commands.add_parser('tinker-check-data', help='Audit chronological Tinker train/validation JSON datasets')
    check.add_argument('--train', type=Path, required=True)
    check.add_argument('--validation', type=Path, required=True)
    predict = commands.add_parser('tinker-predict', help='Sample the frozen checkpoint and log one prospective attempt')
    predict.add_argument('--candidate', required=True)
    predict.add_argument('--case', required=True)
    predict.add_argument('--input', type=Path, required=True)
    predict.add_argument('--manifest', type=Path, required=True)
    quality = commands.add_parser('quality', help='Run offline harness quality/leakage tests and save a receipt')
    quality.add_argument('--mlflow', action='store_true', help='Also export test results to free local MLflow')


def dispatch(args):
    import hashlib
    from .governance import Ledger
    ledger = Ledger()
    if args.command == 'init':
        result = ledger.initialize()
    elif args.command == 'status':
        result = ledger.status_from(ledger.events())
    elif args.command == 'register':
        result = ledger.register(args.name, args.artifact)
    elif args.command == 'enroll':
        result = ledger.enroll(json.loads(args.cohort.read_text()))
    elif args.command == 'record':
        result = ledger.record(args.candidate, args.case, json.loads(args.input.read_text()), args.probabilities, args.error)
    elif args.command == 'resolve':
        result = ledger.resolve(args.match, args.target, args.ended_utc, hashlib.sha256(args.source.read_bytes()).hexdigest())
    elif args.command == 'tinker-check-data':
        from .tinker_bridge import load_datasets
        train, validation = load_datasets(args.train, args.validation, ledger.workspace)
        result = {'train_rows': len(train), 'validation_rows': len(validation), 'audited': True}
    elif args.command == 'tinker-predict':
        import asyncio
        from .tinker_bridge import predict
        result = asyncio.run(predict(ledger.workspace, args.candidate, args.case,
                                    json.loads(args.input.read_text()), args.manifest))
    elif args.command == 'score':
        result = ledger.score()
    elif args.command == 'export-mlflow':
        from .tracking import export_report
        report = ledger._one(ledger.events(), 'report')
        if not report:
            raise ValueError('Explicitly release the report with eval score before exporting')
        result = export_report(report, 'holdout')
    else:
        from .quality import run_quality
        report, path = run_quality()
        result = {'report': str(path), **{k: report[k] for k in ['tests_run', 'passed', 'failures', 'errors']}}
        if args.mlflow:
            from .tracking import export_report
            result['mlflow'] = export_report(report, 'quality')
        print(json.dumps(result, indent=2))
        return 0 if report['passed'] else 1
    print(json.dumps(result, indent=2, allow_nan=False))
    if args.command in ['record', 'tinker-predict'] and result['payload']['status'] == 'failed':
        return 1
    return 0
