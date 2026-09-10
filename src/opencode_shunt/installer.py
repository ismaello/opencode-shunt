"""Install or update the runtime in a repository, and know what is there.

Copying files over whatever was already present is fine once and a liability
afterwards: nothing records which version a repository has, so a repository
installed before a fix keeps the broken version silently. It also cannot tell a
file somebody deliberately edited from one that is merely old, which makes
updating a choice between clobbering local work and never updating.

So each install records a manifest of hashes. Three hashes for a file - what we
ship, what the manifest says we installed, and what is on disk - separate the
cases that matter:

    absent                   -> new, copy it
    matches what we ship     -> current, skip
    matches the manifest     -> old but untouched, safe to replace
    matches neither          -> edited here, leave alone unless forced
    no manifest entry        -> unknowable; say so rather than guess

The last case is the first run against a repository set up by hand. Without a
record there is genuinely no way to tell old from edited, so it reports that it
cannot tell.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
from datetime import datetime, timezone

from . import paths

# Installed by a package manager, or belonging to the machine rather than the repo.
EXCLUDE_DIRS = {"node_modules", "__pycache__", "tests"}
EXCLUDE_FILES = {"package-lock.json", "bun.lock", paths.LOCAL_CONFIG, "shunt.local.example.json"}

GITIGNORE_ENTRIES = [
    "node_modules",
    "package.json",
    "package-lock.json",
    "bun.lock",
    paths.LOCAL_CONFIG,
    paths.MANIFEST_NAME,
]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shipped_files() -> dict[str, pathlib.Path]:
    """Everything we install, keyed by path relative to .opencode."""
    root = paths.runtime()
    if not root.is_dir():
        raise SystemExit(f"the packaged runtime is missing from {root}")
    found = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if EXCLUDE_DIRS & set(relative.parts):
            continue
        if path.name in EXCLUDE_FILES:
            continue
        found[str(relative)] = path
    return found


def classify(source: pathlib.Path, target: pathlib.Path, recorded: str | None) -> str:
    if not target.exists():
        return "new"
    current = sha256(target)
    if current == sha256(source):
        return "current"
    if recorded is None:
        return "unknown"
    if current == recorded:
        return "outdated"
    return "modified"


def read_manifest(destination: pathlib.Path) -> dict:
    path = destination / paths.MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        print(f"warning: {paths.MANIFEST_NAME} is unreadable; treating every file as edited")
        return {}


def write_manifest(destination: pathlib.Path, files: dict[str, pathlib.Path]) -> None:
    (destination / paths.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": paths.version(),
                "installedAt": datetime.now(timezone.utc).isoformat(),
                # Hashes of what we just placed, which is what lets the next run
                # tell an old file from an edited one.
                "files": {name: sha256(files[name]) for name in sorted(files)},
            },
            indent=2,
        )
        + "\n"
    )


def update_gitignore(destination: pathlib.Path) -> str | None:
    """Keep machine-specific and generated files out of version control."""
    path = destination / ".gitignore"
    existing = path.read_text().splitlines() if path.exists() else []
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in existing]
    if not missing:
        return None
    path.write_text("\n".join(existing + missing) + "\n")
    return f".gitignore += {', '.join(missing)}"


def merge_opencode_json(repo: pathlib.Path, providers: dict) -> str | None:
    """Add provider entries to the repository's opencode.json without losing anything.

    The previous installer refused when the file already existed and told the
    user to merge by hand, which is half an installer. Provider entries are
    additive and keyed by name, so merging is well defined: keep what is there,
    add what is missing, and never silently change an entry somebody wrote.
    """
    if not providers:
        return None

    target = repo / "opencode.json"
    if target.is_file():
        try:
            config = json.loads(target.read_text())
        except json.JSONDecodeError as error:
            return f"opencode.json is not valid JSON ({error.msg}); left alone, merge by hand"
    else:
        config = {"$schema": "https://opencode.ai/config.json"}

    existing = config.setdefault("provider", {})
    added = [name for name in providers if name not in existing]
    for name in added:
        existing[name] = providers[name]

    if not added:
        return "opencode.json already had every provider it needs"
    target.write_text(json.dumps(config, indent=2) + "\n")
    return f"opencode.json += provider {', '.join(added)}"


def install(
    repo: pathlib.Path,
    *,
    check: bool = False,
    force: bool = False,
    quiet: bool = False,
) -> int:
    destination = repo / ".opencode"
    manifest = read_manifest(destination)
    recorded = manifest.get("files", {})
    installed = manifest.get("version", "none")
    shipping = paths.version()

    files = shipped_files()
    plan: dict[str, list[str]] = {"new": [], "current": [], "outdated": [], "modified": [], "unknown": []}
    for relative, source in sorted(files.items()):
        plan[classify(source, destination / relative, recorded.get(relative))].append(relative)

    orphans = [name for name in recorded if name not in files and (destination / name).exists()]

    if not quiet:
        print(f"repository: {repo}")
        print(f"installed:  {installed}    shipping: {shipping}\n")
        for state, label in (
            ("new", "to install"),
            ("outdated", "to update"),
            ("modified", "edited here"),
            ("unknown", "differs, with no record of what was installed"),
        ):
            if plan[state]:
                print(f"{label}:")
                for name in plan[state]:
                    print(f"  {name}")
        if plan["current"]:
            print(f"already current: {len(plan['current'])} files")
        if orphans:
            print("no longer shipped, left in place:")
            for name in orphans:
                print(f"  {name}")

    held_back = plan["modified"] + plan["unknown"]
    will_write = plan["new"] + plan["outdated"] + (held_back if force else [])

    if held_back and not force and not quiet:
        if plan["unknown"]:
            print(
                f"\n{len(plan['unknown'])} file(s) differ from what is shipped, but nothing recorded "
                f"what was installed,\nso whether they are old or deliberately edited cannot be told "
                f"from here. Normal on a\nfirst run against a hand-made setup; one --force makes the "
                f"distinction exact afterwards."
            )
        print("Re-run with --force to take the shipped version; each replacement keeps a .bak.")

    if check:
        if will_write or held_back:
            print(f"\nout of date: {len(will_write + held_back)} file(s) differ.")
            return 1
        print("\nup to date.")
        return 0

    for relative in will_write:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative in held_back:
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        shutil.copy2(files[relative], target)

    destination.mkdir(parents=True, exist_ok=True)
    write_manifest(destination, files)
    note = update_gitignore(destination)

    if not quiet:
        print(f"\nwrote {len(will_write)} file(s)." if will_write else "\nnothing to write.")
        if note:
            print(note)
        print(f"installed {shipping} into {destination}")
    return 0
