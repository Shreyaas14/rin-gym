"""Execute the offline eval contract tests and save an independent quality receipt."""
import io
import hashlib
import json
from pathlib import Path
import unittest
import uuid

from rin_gym.paths import workspace_root
from .governance import code_fingerprint, utc_now


def run_quality(workspace=None):
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        'app.forecast.tests.test_harness',
        'app.forecast.tests.test_quality',
        'app.forecast.tests.test_governance',
        'app.forecast.tests.test_tinker_bridge',
    ])
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    tests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in Path(__file__).with_name('tests').glob('test_*.py')}
    report = {'recorded_utc': utc_now(), 'tests_run': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors),
              'skipped': len(result.skipped), 'passed': result.wasSuccessful(),
              'code_sha256': code_fingerprint(), 'test_sha256': tests,
              'test_output': stream.getvalue(),
              'scope': 'Harness correctness and leakage regression tests; not evidence of generalization'}
    root = Path(workspace) if workspace is not None else workspace_root()
    path = root / 'artifacts' / 'eval-quality' / (uuid.uuid4().hex + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as file:
        json.dump(report, file, indent=2, allow_nan=False)
    return report, path
