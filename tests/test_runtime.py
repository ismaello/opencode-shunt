"""Run the TypeScript test suites through pytest.

The pure functions in the runtime are where the subtle failures live: coverage
parsing, the delegation break-even, and the guards that stop a worker model
deleting code. They have their own deterministic suites, and wiring them in here
means one command covers the whole project. A suite nobody runs is worse than no
suite, because it implies a guarantee it is not providing.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from opencode_shunt import paths

SUITES = ["coverage", "economics", "guards"]


@pytest.mark.parametrize("suite", SUITES)
def test_runtime_suite(suite: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed, so the runtime suites cannot run")

    script = paths.runtime() / "tests" / f"{suite}.test.mjs"
    assert script.is_file(), f"{script} is missing from the package"

    result = subprocess.run(
        # Type stripping lets the tests import the .ts modules directly, so
        # there is no build step to go stale between the code and its tests.
        [node, "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=paths.runtime(),
    )
    if result.returncode != 0:
        pytest.fail(f"{suite} suite failed:\n{result.stdout[-3000:]}\n{result.stderr[-1000:]}")
