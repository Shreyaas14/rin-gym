"""Real optional MLflow integration against a disposable local SQLite database."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.forecast.tracking import export_report


@unittest.skipUnless(importlib.util.find_spec('mlflow'), 'install the optional tracking extra')
class TrackingTests(unittest.TestCase):
    def test_quality_export_ignores_remote_environment_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {
            'MLFLOW_TRACKING_URI': 'https://must-not-be-contacted.invalid',
            'MLFLOW_DISABLE_TELEMETRY': 'true',
        }):
            report = {'tests_run': 7, 'failures': 0, 'errors': 0, 'scope': 'synthetic integration fixture'}
            receipt = export_report(report, 'quality', tmp)
            from mlflow.tracking import MlflowClient
            client = MlflowClient(tracking_uri=receipt['tracking_uri'], registry_uri=receipt['tracking_uri'])
            run = client.get_run(receipt['run_id'])
            self.assertEqual(run.info.status, 'FINISHED')
            self.assertEqual(run.data.metrics['tests_run'], 7)
            artifact = client.download_artifacts(receipt['run_id'], 'report.json')
            self.assertEqual(json.loads(Path(artifact).read_text()), report)
            self.assertTrue(receipt['tracking_uri'].startswith('sqlite:///'))
            self.assertTrue(run.info.artifact_uri.startswith(Path(tmp).resolve().as_uri()))

    def test_holdout_export_preserves_loss_orientation_and_coverage_gate(self):
        report = {'cohort': {'baseline': 'baseline:v1', 'candidate': 'candidate:v1', 'protocol_sha256': 'a' * 64},
                  'metrics_common_cases_only': {
                      'baseline:v1': {'brier': .6, 'log_loss': 1.2, 'games': 2, 'snapshots': 3},
                      'candidate:v1': {'brier': .3, 'log_loss': .8, 'games': 2, 'snapshots': 3}},
                  'gates': {'complete_coverage': False, 'improved': False}}
        with tempfile.TemporaryDirectory() as tmp:
            receipt = export_report(report, 'holdout', tmp)
            from mlflow.tracking import MlflowClient
            run = MlflowClient(tracking_uri=receipt['tracking_uri']).get_run(receipt['run_id'])
            self.assertEqual(run.data.metrics['candidate/brier'], .3)
            self.assertEqual(run.data.metrics['gate/complete_coverage'], 0)
            self.assertEqual(run.data.params['candidate'], 'candidate:v1')
