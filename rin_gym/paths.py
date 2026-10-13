"""Keep writable research data outside the installed Python package."""
import os
from importlib.resources import files
from pathlib import Path


def workspace_root():
    """Use the explicit workspace, or the caller's current working directory."""
    return Path(os.environ.get("RIN_GYM_WORKSPACE", Path.cwd())).expanduser().resolve()


def seed_sports_manifests():
    """Copy frozen cohort selections only when the workspace lacks them."""
    destination = workspace_root() / "data" / "manifests" / "sports"
    destination.mkdir(parents=True, exist_ok=True)
    resources = files("rin_gym").joinpath("resources", "sports")
    for name in ("nba_sample_markets.json", "soccer_sample_markets.json"):
        target = destination / name
        if not target.exists():
            target.write_bytes(resources.joinpath(name).read_bytes())
