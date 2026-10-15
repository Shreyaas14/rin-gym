"""Verify exact research snapshots and optionally run their offline checks."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_path(name, experiment):
    path = PurePosixPath(name)
    require(not path.is_absolute() and '\\' not in name, 'Unsafe archive path')
    require('..' not in path.parts and path.parts[0] == experiment, 'Unsafe member prefix')
    return path


def verify_and_extract(destination):
    indexes = json.loads((ROOT / 'archives/index.json').read_text())
    experiments = set()
    total = 0
    for summary in indexes:
        name = summary['experiment']
        require(name not in experiments, 'Duplicate experiment')
        experiments.add(name)
        index = json.loads((ROOT / 'archives' / (name + '.manifest.json')).read_text())
        require({k: v for k, v in index.items() if k != 'files'} == summary, 'Index mismatch')
        archive = ROOT / index['archive']['path']
        require(archive.parent == ROOT / 'archives', 'Unexpected archive location')
        require(archive.stat().st_size == index['archive']['bytes'], 'Archive size mismatch')
        require(sha(archive) == index['archive']['sha256'], 'Archive hash mismatch')
        expected = {r['path']: r for r in index['files']}
        require(len(expected) == len(index['files']) == index['file_count'], 'Duplicate manifest member')
        seen = set()
        with tarfile.open(archive, 'r:gz') as tar:
            for member in tar:
                require(member.isfile(), 'Only regular files are allowed')
                relative = safe_path(member.name, name)
                require(member.name not in seen and member.name in expected, 'Unexpected or duplicate archive member')
                seen.add(member.name)
                row = expected[member.name]
                require(member.size == row['bytes'], 'Member size mismatch: ' + member.name)
                target = destination.joinpath(*relative.parts)
                require(target.resolve().is_relative_to(destination.resolve()), 'Extraction escaped destination')
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = tar.extractfile(member)
                require(stream is not None, 'Missing member body')
                h = hashlib.sha256()
                with stream, target.open('xb') as output:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(chunk)
                        output.write(chunk)
                require(h.hexdigest() == row['sha256'], 'Member hash mismatch: ' + member.name)
                readable = ROOT.joinpath(*relative.parts)
                if readable.is_file():
                    require(sha(readable) == row['sha256'], 'Readable copy changed: ' + member.name)
        require(seen == set(expected), 'Missing archive members')
        require(sum(r['bytes'] for r in expected.values()) == index['uncompressed_bytes'], 'Uncompressed-size mismatch')
        total += len(seen)
        print(json.dumps({'snapshot': name, 'files': len(seen), 'archive_bytes': archive.stat().st_size, 'verified': True}), flush=True)
    # The readable SQLite export must agree with the archived ledger.
    import sqlite3
    ledger_path = destination / 'market-entity-resolution-v2/budget.sqlite'
    with sqlite3.connect('file:' + str(ledger_path) + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        ledger = {'meta': [dict(r) for r in db.execute('SELECT * FROM meta ORDER BY key')],
                  'calls': [dict(r) for r in db.execute('SELECT * FROM calls ORDER BY created,id')]}
    readable_ledger = json.loads((ROOT / 'market-entity-resolution-v2/budget-ledger.json').read_text())
    require(ledger == readable_ledger, 'Readable budget ledger differs from archived SQLite')
    print(json.dumps({'total_verified_files': total, 'readable_budget_export': 'PASS'}), flush=True)


def run_checks(destination):
    experiment = destination / 'market-entity-resolution-v2'
    subprocess.run([sys.executable, str(experiment / 'verify.py')], cwd=destination, check=True)
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(experiment),
                    '-p', 'test_benchmark.py', '-v'], cwd=destination, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extract', type=Path, help='Retain snapshots in a new or empty directory')
    parser.add_argument('--run-checks', action='store_true', help='Run saved integrity audit and offline tests')
    args = parser.parse_args()
    if args.extract:
        destination = args.extract.resolve()
        require(not destination.exists() or (destination.is_dir() and not any(destination.iterdir())),
                'Extraction destination must be absent or empty')
        destination.mkdir(parents=True, exist_ok=True)
        verify_and_extract(destination)
        if args.run_checks:
            run_checks(destination)
    else:
        with tempfile.TemporaryDirectory(prefix='market-resolution-evidence-') as tmp:
            destination = Path(tmp)
            verify_and_extract(destination)
            if args.run_checks:
                run_checks(destination)


if __name__ == '__main__':
    main()
