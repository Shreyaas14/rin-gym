"""Resolve writable forecast and evaluation data outside the package."""
import os
from pathlib import Path


def workspace_root():
    return Path(os.environ.get("RIN_GYM_WORKSPACE", Path.cwd())).expanduser().resolve()
