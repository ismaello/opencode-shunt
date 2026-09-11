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


def opencode_models() -> pathlib.Path:
    """OpenCode's own table of models and what they cost.

    Read because the alternative does not work. A price list maintained by hand
    here goes stale the week a vendor ships, and stale prices are not a
    cosmetic problem: the delegation floor is computed from them, so an
    orchestrator priced as a model two generations old delegates at the wrong
    threshold on every call, silently. Caught in exactly that state - the
    catalogue offered Gemini 2.5 Pro while 3.1 Pro was current, and priced
    GPT-5.2 at 5.1's rates.

    Not a documented interface, so every read is defensive and the baked-in
    table stays as the fallback.
    """
    if override := os.environ.get("OPENCODE_MODELS"):
        return pathlib.Path(override)
    cache = os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
    return pathlib.Path(cache) / "opencode" / "models.json"


def sessions_in(repo: pathlib.Path) -> set[str] | None:
    """Session ids recorded against one repository, or None if unknowable.

    Telemetry is written to a single file per machine, so every command that
    reads it reports on every repository at once unless it filters. That was a
    real defect rather than a cosmetic one: a freshly installed repo had
    `doctor` announcing 77 delegations and 248k tokens saved, all of it earned
    somewhere else. A health check that reports activity which did not happen
    here is telling the user something untrue about the thing they just
    installed.

    OpenCode records the directory a session ran in, which is the only link
    between a telemetry line and a repository. Returning None when the database
    is unreadable means "cannot filter", and callers then report everything and
    say so - better than silently reporting nothing.
    """
    database = opencode_db()
    if not database.is_file():
        return None
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        return {
            sid for (sid,) in connection.execute(
                "SELECT id FROM session WHERE directory = ?", (str(repo),)
            )
        }
    except Exception:
        return None


def resolve_repo(argument: str | None) -> pathlib.Path:
    repo = pathlib.Path(argument or ".").expanduser().resolve()
    if not repo.is_dir():
        raise SystemExit(f"no such directory: {repo}")
    return repo
