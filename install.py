#!/usr/bin/env python3
"""Install or update the shunt in a repository.

The previous installer copied files over whatever was there. That is fine once
and a liability afterwards: nothing recorded what version a repository had, so a
repository installed before a fix silently kept the broken version and there was
no way to find out short of diffing by hand. It also could not tell a file the
user had deliberately edited from one that was merely old, so updating meant
choosing between clobbering local work and never updating at all.

So each install records a manifest of file hashes. Comparing three hashes for a
file - what we ship, what the manifest says we installed, and what is on disk
now - distinguishes the four cases that matter:

    absent                          -> new, copy it
    matches what we ship            -> already current, skip
    matches the manifest            -> old but untouched, safe to replace
    matches neither                 -> edited locally, leave alone unless forced
    no manifest entry at all        -> unknowable, treat as edited and say so

That last case is the first run against a repository set up by hand. There is
genuinely no way to tell an old file from an edited one without a record, so the
installer says it cannot tell rather than guessing.

Usage:
    ./install.py /path/to/repo           install or update
    ./install.py /path/to/repo --check   report only, change nothing
    ./install.py /path/to/repo --force   replace locally edited files too
"""

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
from datetime import datetime, timezone

SOURCE = pathlib.Path(__file__).resolve().parent
MANIFEST_NAME = ".shunt-manifest.json"

# Installed by the package manager, or belonging to the machine rather than the repo.
EXCLUDE_DIRS = {"node_modules", "__pycache__"}
EXCLUDE_FILES = {"package-lock.json", "bun.lock", "shunt.local.json"}

# Written once on a fresh install and never touched again.
LOCAL_CONFIG = "shunt.local.json"
LOCAL_EXAMPLE = "shunt.local.example.json"

GITIGNORE_ENTRIES = [
    "node_modules",
    "package.json",
    "package-lock.json",
    "bun.lock",
    ".gitignore",
    LOCAL_CONFIG,
]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, pathlib.Path]:
    """Every file we ship, keyed by its path relative to .opencode."""
    root = SOURCE / "opencode"
    if not root.is_dir():
        sys.exit(f"no opencode/ directory beside {__file__}")
    found = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if EXCLUDE_DIRS & set(path.relative_to(root).parts):
            continue
        if path.name in EXCLUDE_FILES:
            continue
        found[str(path.relative_to(root))] = path
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


def seed_local_config(destination: pathlib.Path, dry_run: bool) -> str | None:
    """Give a fresh install a working local config instead of a broken one."""
    target = destination / LOCAL_CONFIG
    if target.exists():
        return None
    example = SOURCE / "opencode" / LOCAL_EXAMPLE
    if not example.is_file():
        return None

    config = json.loads(example.read_text())
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_VERTEX_PROJECT")
    filled = False
    if project:
        for profile in config.get("profiles", {}).values():
            if str(profile.get("project", "")).startswith("CHANGE-ME"):
                profile["project"] = project
                filled = True
    config.pop("_notes", None)

    if not dry_run:
        target.write_text(json.dumps(config, indent=2) + "\n")
    if filled:
        return f"created {LOCAL_CONFIG} using GOOGLE_CLOUD_PROJECT={project}"
    return f"created {LOCAL_CONFIG}; edit it to set your project id and active profile"


def update_gitignore(destination: pathlib.Path, dry_run: bool) -> str | None:
    path = destination / ".gitignore"
    existing = path.read_text().splitlines() if path.exists() else []
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in existing]
    if not missing:
        return None
    if not dry_run:
        lines = existing + missing
        path.write_text("\n".join(lines) + "\n")
    return f".gitignore += {', '.join(missing)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="repository to install into")
    parser.add_argument("--check", action="store_true", help="report what would change and exit")
    parser.add_argument("--force", action="store_true", help="also replace locally edited files")
    args = parser.parse_args()

    repo = pathlib.Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        sys.exit(f"no such directory: {repo}")

    version = (SOURCE / "VERSION").read_text().strip() if (SOURCE / "VERSION").is_file() else "unknown"
    destination = repo / ".opencode"
    manifest_path = destination / MANIFEST_NAME

    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            print(f"warning: {MANIFEST_NAME} is unreadable; treating every file as locally edited")
    installed_version = manifest.get("version", "none")
    recorded = manifest.get("files", {})

    files = source_files()
    plan: dict[str, list[str]] = {"new": [], "current": [], "outdated": [], "modified": [], "unknown": []}
    for relative, source in sorted(files.items()):
        plan[classify(source, destination / relative, recorded.get(relative))].append(relative)

    # Files a previous version installed that we no longer ship.
    orphans = [name for name in recorded if name not in files and (destination / name).exists()]

    print(f"repository: {repo}")
    print(f"installed:  {installed_version}    shipping: {version}\n")

    for state, label in [
        ("new", "to install"),
        ("outdated", "to update"),
        ("modified", "edited locally"),
        ("unknown", "differs, but no record of what was installed"),
        ("current", "already current"),
    ]:
        names = plan[state]
        if not names:
            continue
        if state == "current":
            print(f"{label}: {len(names)} files")
            continue
        print(f"{label}:")
        for name in names:
            print(f"  {name}")
    if orphans:
        print("no longer shipped (left in place):")
        for name in orphans:
            print(f"  {name}")

    held_back = plan["modified"] + plan["unknown"]
    will_write = plan["new"] + plan["outdated"] + (held_back if args.force else [])

    if plan["modified"] and not args.force:
        print(
            f"\n{len(plan['modified'])} file(s) differ from both the shipped and the recorded version, "
            f"so they were edited here. Left untouched."
        )
    if plan["unknown"] and not args.force:
        print(
            f"\n{len(plan['unknown'])} file(s) differ from what is shipped, but no manifest recorded what "
            f"was installed, so whether they are merely old or deliberately edited cannot be told from here. "
            f"Left untouched. This is normal on the first run against a repository set up by hand; after one "
            f"--force the distinction becomes exact."
        )
    if held_back and not args.force:
        print("Re-run with --force to take the shipped version; each replaced file is kept as .bak.")

    if args.check:
        if will_write or held_back:
            print(f"\nout of date: {len(will_write + held_back)} file(s) differ.")
            return 1
        print("\nup to date.")
        return 0

    if not will_write:
        print("\nnothing to do.")
    for relative in will_write:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative in held_back:
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        shutil.copy2(files[relative], target)
    if will_write:
        print(f"\nwrote {len(will_write)} file(s).")

    for note in (seed_local_config(destination, dry_run=False), update_gitignore(destination, dry_run=False)):
        if note:
            print(note)

    manifest_path.write_text(
        json.dumps(
            {
                "version": version,
                "installedAt": datetime.now(timezone.utc).isoformat(),
                "source": str(SOURCE),
                # Hashes of what we just placed, which is what makes the next run
                # able to tell an old file from an edited one.
                "files": {relative: sha256(files[relative]) for relative in sorted(files)},
            },
            indent=2,
        )
        + "\n"
    )

    provider_config = SOURCE / "opencode.project.json"
    if provider_config.is_file():
        target = repo / "opencode.json"
        if target.exists():
            print(f"opencode.json exists, left alone. Merge the provider block from {provider_config} if needed.")
        else:
            shutil.copy2(provider_config, target)
            print("wrote opencode.json (provider configuration)")

    print(f"\ninstalled version {version} into {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
