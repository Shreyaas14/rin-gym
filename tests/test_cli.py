"""Installed CLI and import contracts; run with uv run python -m unittest discover."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CLITests(unittest.TestCase):
    def invoke(self, *args, cwd, workspace=None):
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        env.pop('RIN_GYM_WORKSPACE', None)
        if workspace is not None:
            env['RIN_GYM_WORKSPACE'] = str(workspace)
        return subprocess.run([sys.executable, '-m', 'rin_gym', *args], cwd=cwd,
                              env=env, capture_output=True, text=True)

    def test_all_command_help_works_without_data_or_writes(self):
        commands = [[], ['forecast'], ['eval']]
        commands += [['forecast', command] for command in ['prepare', 'run', 'verify', 'simulate']]
        commands += [['eval', command] for command in ['init', 'status', 'register', 'enroll', 'record', 'resolve', 'score', 'export-mlflow', 'quality', 'tinker-check-data', 'tinker-predict']]
        with tempfile.TemporaryDirectory() as tmp:
            for command in commands:
                result = self.invoke(*command, '--help', cwd=tmp)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_imports_do_not_read_inputs_or_create_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, 'RIN_GYM_WORKSPACE': tmp}
            result = subprocess.run([sys.executable, '-c',
                'import app.forecast.__main__; import app.forecast.governance; import app.forecast.tracking'],
                cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_workspace_flag_overrides_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.invoke('--workspace', str(root / 'selected'), 'forecast', 'run',
                                 cwd=tmp, workspace=root / 'ignored')
            self.assertEqual(result.returncode, 1)
            self.assertIn(str(root / 'selected' / 'data' / 'manifests' / 'forecast'), result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse((root / 'ignored').exists())

    def test_invalid_simulation_count_fails_before_loading_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.invoke('forecast', 'simulate', '--match', '400021444', '--utc',
                                 '2026-06-25T02:30:00Z', '--runs', '0', cwd=tmp)
            self.assertEqual(result.returncode, 2)
            self.assertIn('--runs must be positive', result.stderr)

    def test_eval_init_is_pending_and_cannot_score_without_enrollment(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.invoke('eval', 'init', cwd=tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['state'], 'pending_enrollment')
            result = self.invoke('eval', 'score', cwd=tmp)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('Traceback', result.stderr)
            self.assertIn('No holdout', result.stderr)

    def test_eval_quality_is_offline_and_writes_a_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.invoke('eval', 'quality', cwd=tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['passed'])
            self.assertTrue(Path(report['report']).exists())


if __name__ == '__main__':
    unittest.main()
