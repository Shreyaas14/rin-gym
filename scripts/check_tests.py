"""Run every available suite with one failure-sensitive command."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
suites = [root / 'app/forecast/tests']
tests = root / 'tests'
if tests.is_dir():
    suites += sorted(path for path in tests.iterdir() if path.is_dir())
suites.append(tests)
for directory in suites:
    if directory.is_dir() and list(directory.glob('test*.py')):
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(directory), '-v'],
                       cwd=Path.cwd() if '--installed' in sys.argv else root, check=True)
