"""Run every available suite with one failure-sensitive command."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
for relative in ['app/forecast/tests', 'tests/fifa', 'tests/replay', 'tests']:
    directory = root / relative
    if directory.is_dir() and list(directory.glob('test*.py')):
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(directory), '-v'], cwd=Path.cwd() if '--installed' in sys.argv else root, check=True)
