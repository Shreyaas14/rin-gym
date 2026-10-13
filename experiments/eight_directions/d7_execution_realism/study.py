"""Reproduce direction 7 through the owned execution-aware offline harness."""

from pathlib import Path
import os
import argparse
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
IMPLEMENTATION = ROOT.parent / "rin-gym-execution-aware"
OUT = ROOT / "artifacts/eight-directions/d7_execution_realism"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(IMPLEMENTATION), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "experiments.execution_aware.evaluate",
            "--source",
            str(ROOT),
            "--out",
            str(OUT),
        ]
        + (["--verify"] if args.verify else []),
        cwd=IMPLEMENTATION,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    (OUT / ("audit-receipt.txt" if args.verify else "cli-receipt.txt")).write_text(
        result.stdout
    )
    print(result.stdout, end="")


if __name__ == "__main__":
    main()
