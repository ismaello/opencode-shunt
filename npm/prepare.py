#!/usr/bin/env python3
"""Build the npm package from the Python one.

The npm package is a wrapper: it carries no logic of its own, only the Python
distribution and a launcher. Building it by hand invites the one failure this
script exists to prevent - an npm version that does not match the Python
version inside it, so `npx opencode-shunt@3.2.0` silently runs 3.1.0.

    python npm/prepare.py            # build, then: npm publish ./npm
    python npm/prepare.py --pack     # build and pack a tarball, for testing
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NPM = ROOT / "npm"


def python_version() -> str:
    """The single source of truth for the version, read rather than repeated."""
    source = (ROOT / "src" / "opencode_shunt" / "__init__.py").read_text()
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', source)
    if not match:
        sys.exit("could not find __version__ in src/opencode_shunt/__init__.py")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", action="store_true", help="also npm pack, for local testing")
    args = parser.parse_args()

    version = python_version()

    print(f"building the Python wheel for {version}")
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    subprocess.run([sys.executable, "-m", "build"], cwd=ROOT, check=True)

    # Only the wheel: installing it needs no build backend at the user's end,
    # which is one less thing that can fail on a machine we never see.
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        sys.exit(f"expected exactly one wheel, found {[w.name for w in wheels]}")

    staged = NPM / "dist"
    shutil.rmtree(staged, ignore_errors=True)
    staged.mkdir(parents=True)
    shutil.copy2(wheels[0], staged / wheels[0].name)
    print(f"staged {wheels[0].name}")

    manifest = NPM / "package.json"
    data = json.loads(manifest.read_text())
    data["version"] = version
    manifest.write_text(json.dumps(data, indent=2) + "\n")
    print(f"npm version set to {version}")

    shutil.copy2(ROOT / "LICENSE", NPM / "LICENSE")

    if args.pack:
        subprocess.run(["npm", "pack"], cwd=NPM, check=True)

    print(f"\nready. Publish with:  npm publish ./npm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
