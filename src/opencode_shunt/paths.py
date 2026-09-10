"""Where everything lives.

One module so that no other file has to guess, and so that the two directories
that are not ours - OpenCode's data directory and the shunt's own telemetry -
are named in exactly one place.
"""

from __future__ import annotations

import os
import pathlib

MANIFEST_NAME = ".shunt-manifest.json"
LOCAL_CONFIG = "shunt.local.json"


def version() -> str:
    from . import __version__

    return __version__


def runtime() -> pathlib.Path:
    """The TypeScript OpenCode loads, shipped inside this package."""
    return pathlib.Path(__file__).resolve().parent / "runtime"


def shunt_data() -> pathlib.Path:
    """Telemetry, backups and stored command output."""
    return pathlib.Path(
        os.environ.get("SHUNT_DATA_DIR") or pathlib.Path.home() / ".local/share/opencode-shunt"
    )


def telemetry_file() -> pathlib.Path:
    return shunt_data() / "telemetry.jsonl"


def opencode_db() -> pathlib.Path:
    """OpenCode's own session accounting, used to measure real savings.

    Not a documented interface, so it is read-only and every caller has to cope
    with it being absent or shaped differently than expected.
    """
    if override := os.environ.get("OPENCODE_DB"):
        return pathlib.Path(override)
    return pathlib.Path.home() / ".local/share/opencode/opencode.db"


def resolve_repo(argument: str | None) -> pathlib.Path:
    repo = pathlib.Path(argument or ".").expanduser().resolve()
    if not repo.is_dir():
        raise SystemExit(f"no such directory: {repo}")
    return repo
