"""Opt-in free MLflow tracking: local SQLite metadata and local artifacts only."""
import hashlib
import json
import os
from pathlib import Path

from rin_gym.paths import workspace_root


def export_report(report, kind, workspace=None):
    os.environ['MLFLOW_DISABLE_TELEMETRY'] = 'true'
    try:
        from mlflow.tracking import MlflowClient
    except ImportError as exc:
        raise ValueError('Install free local tracking with uv sync --extra tracking') from exc
    root = (Path(workspace) if workspace is not None else workspace_root()).resolve() / 'artifacts' / 'mlflow'
    root.mkdir(parents=True, exist_ok=True)
    uri = 'sqlite:///' + str(root / 'tracking.db')
    # Explicit local URI avoids accidentally using an environment-configured remote server.
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    experiment_name = 'rin-gym-' + kind
    artifacts = root / kind
    expected_uri = artifacts.as_uri()
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        experiment_id = client.create_experiment(experiment_name, artifact_location=expected_uri)
    else:
        if experiment.artifact_location != expected_uri or experiment.lifecycle_stage != 'active':
            raise ValueError('Expected an active experiment with the local artifact directory')
        experiment_id = experiment.experiment_id
    report_hash = hashlib.sha256(json.dumps(report, sort_keys=True, allow_nan=False).encode()).hexdigest()
    run = client.create_run(experiment_id, tags={'report_sha256': report_hash, 'evidence_kind': kind})
    run_id = run.info.run_id
    try:
        if kind == 'holdout':
            for name, values in report['metrics_common_cases_only'].items():
                role = 'baseline' if name == report['cohort']['baseline'] else 'candidate'
                client.log_param(run_id, role, name)
                for metric in ['brier', 'log_loss', 'games', 'snapshots']:
                    client.log_metric(run_id, role + '/' + metric, values[metric])
            for name, value in report['gates'].items():
                client.log_metric(run_id, 'gate/' + name, float(value))
            client.log_param(run_id, 'protocol_sha256', report['cohort']['protocol_sha256'])
            client.set_tag(run_id, 'aggregation', 'equal-match-weighted; common cases only; coverage gates required')
        else:
            client.log_metric(run_id, 'tests_run', report['tests_run'])
            client.log_metric(run_id, 'failures', report['failures'])
            client.log_metric(run_id, 'errors', report['errors'])
            client.set_tag(run_id, 'evidence_scope', 'harness correctness only; not generalization evidence')
        client.log_dict(run_id, report, 'report.json')
        client.set_terminated(run_id, status='FINISHED')
    except Exception:
        client.set_terminated(run_id, status='FAILED')
        raise
    return {'run_id': run_id, 'tracking_uri': uri, 'artifacts': str(artifacts)}
