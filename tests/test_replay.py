"""Tests for the counterfactual replay, and for the duplicated formula under it.

The first test here is the one that matters. The break-even arithmetic exists
twice - TypeScript for the runtime, Python for this command - and two copies of
a formula drift apart. This one is not a display formula: it decides whether
work gets delegated, so a drift would change behaviour silently and in the
expensive direction. So the implementations are run against each other over a
spread of inputs, including the ones where languages usually differ: rounding of
exact halves, and zero.

The rest guard the reason the command exists at all. Replaying history is worth
having because it is free and has no variance; it is worth *trusting* only if it
refuses to dress a small modelled number up as a finding.
"""

from __future__ import annotations

import json
import shutil
import math
import subprocess

import pytest

from opencode_shunt import paths
from opencode_shunt.economics import Economics, assess
from opencode_shunt.replay import FIXED_MAX_BYTES, FIXED_MAX_LINES, in_band, main

# --- the two implementations have to agree -----------------------------------

CASES = [
    {},
    {"remainingTurns": 20},
    {"remainingTurns": 2, "assumedConversationTokens": 18_000},
    # Gemini Pro: a much flatter cache write to read ratio, which is what moves
    # the floor. If the two implementations diverge anywhere, it is here.
    {"cacheWritePerMillion": 1.25, "cacheReadPerMillion": 0.3125},
    {"expectedCompression": 0.5, "safetyMargin": 1.0},
    {"charsPerToken": 3.5, "extraTurnsPerDelegation": 1},
    # A worker that is not free, which is the usual case and was missing from
    # the arithmetic entirely.
    {"workerInPerMillion": 0.30, "workerOutPerMillion": 2.50},
    # A worker dearer per token than the context it spares: no size pays, and
    # both sides have to agree that the threshold is infinite rather than
    # producing a large finite number or a negative one.
    {"workerInPerMillion": 40.0, "workerOutPerMillion": 80.0},
]
SIZES = [0, 1, 3_373, 9_930, 9_931, 12_144, 24_171, 60_486, 1_000_000]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the TypeScript original")
def test_python_agrees_with_the_typescript_it_duplicates():
    script = """
import { assess, loadEconomics } from "./lib/economics.ts"
const { cases, sizes } = JSON.parse(process.env.INPUT)
const out = []
for (const overrides of cases) {
  const economics = loadEconomics(overrides)
  for (const size of sizes) {
    const v = assess(size, economics)
    out.push([v.breakEvenChars, Number(v.net.toFixed(8)), v.worthwhile])
  }
}
console.log(JSON.stringify(out))
"""
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        cwd=paths.runtime(),
        capture_output=True,
        text=True,
        env={"INPUT": json.dumps({"cases": CASES, "sizes": SIZES}), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    if result.returncode != 0:
        pytest.skip(f"could not run the TypeScript: {result.stderr[:200]}")

    expected = json.loads(result.stdout)
    actual = []
    for overrides in CASES:
        economics = Economics.from_config(overrides)
        for size in SIZES:
            verdict = assess(size, economics)
            # JSON has no infinity, so JSON.stringify writes null where the
            # threshold is unreachable. Same meaning, different spelling.
            floor = None if verdict.break_even_chars == math.inf else verdict.break_even_chars
            actual.append([floor, round(verdict.net, 8), verdict.worthwhile])

    assert actual == expected
    assert any(row[0] is None for row in actual), "the unreachable-threshold case stopped being exercised"


def test_a_config_block_maps_onto_the_dataclass():
    economics = Economics.from_config({"remainingTurns": 9, "cacheReadPerMillion": 0.25})
    assert economics.remaining_turns == 9
    assert economics.cache_read_per_million == 0.25
    # Untouched keys keep the shipped defaults rather than becoming zero.
    assert economics.cache_write_per_million == 6.25


def test_junk_in_the_config_does_not_become_a_threshold_of_zero():
    """A string where a number belongs must not silently zero the floor."""
    economics = Economics.from_config({"remainingTurns": "muchos", "nonsense": 1})
    assert economics.remaining_turns == Economics().remaining_turns


# --- which reads could change verdict ----------------------------------------


def read(byte_count: int, lines: int = 100) -> dict:
    return {"bytes": byte_count, "lines": lines}


def test_a_read_the_fixed_floor_already_caught_is_not_counted_again():
    """It was blocked. Lowering the floor changes nothing about it."""
    assert not in_band(read(FIXED_MAX_BYTES + 1), floor=10_000)
    assert not in_band(read(20_000, lines=FIXED_MAX_LINES + 1), floor=10_000)


def test_a_read_below_the_tested_floor_is_left_alone():
    """The 3,373-byte batch that bulk_read refused, correctly."""
    assert not in_band(read(3_373), floor=9_931)


def test_a_read_in_the_band_is_what_the_command_is_looking_for():
    """12 KB: allowed for being small, yet above the economic floor."""
    assert in_band(read(12_144), floor=9_931)


def test_the_floor_itself_counts_as_in_band():
    assert in_band(read(9_931), floor=9_931)
    assert not in_band(read(9_930), floor=9_931)


# --- the honesty of the output -----------------------------------------------


@pytest.fixture
def history(tmp_path, monkeypatch):
    """A fake telemetry log and session database in a disposable directory."""
    import sqlite3

    telemetry = tmp_path / "telemetry.jsonl"
    database = tmp_path / "opencode.db"
    monkeypatch.setattr(paths, "telemetry_file", lambda: telemetry)
    monkeypatch.setattr(paths, "opencode_db", lambda: database)

    # Pin the economics, so the floor these tests are judged against is a stated
    # 9,931 bytes rather than whatever the shipped defaults happen to be.
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".opencode" / "shunt.json").write_text(
        json.dumps(
            {
                "economics": {
                    "assumedConversationTokens": 18_000,
                    "remainingTurns": 2,
                    "cacheWritePerMillion": 6.25,
                    "cacheReadPerMillion": 0.5,
                }
            }
        )
    )

    def write(reads: list[dict], cost: float = 1.0) -> None:
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE session (id TEXT, directory TEXT, cost REAL)")
        connection.execute("INSERT INTO session VALUES (?, ?, ?)", ("ses_1", str(tmp_path), cost))
        connection.commit()
        telemetry.write_text(
            "\n".join(
                json.dumps({"tool": "read", "verdict": "allow-small", "sessionID": "ses_1", **r})
                for r in reads
            )
        )

    return write, tmp_path


def test_a_small_effect_is_reported_as_not_worth_an_ab(history, capsys):
    """The whole reason this command exists instead of a benchmark."""
    write, tmp_path = history
    write([read(12_144)], cost=10.0)
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "NOT WORTH AN A/B" in out
    # It has to say why, or it is just an assertion.
    assert "146%" in out


def test_a_large_effect_is_reported_as_measurable(history, capsys):
    write, tmp_path = history
    write([read(30_000), read(35_000)], cost=0.01)
    main([str(tmp_path)])
    assert "An A/B could resolve this" in capsys.readouterr().out


def test_the_limits_are_stated_whatever_the_verdict(history, capsys):
    """It re-judges records; it cannot know what the model would have done."""
    write, tmp_path = history
    write([read(12_144)])
    main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "does not tell you" in out
    assert "ceiling on a decision" in out


def test_an_empty_history_says_so_rather_than_printing_zeroes(history, capsys):
    write, tmp_path = history
    write([])
    assert main([str(tmp_path)]) == 0
    assert "no reads recorded" in capsys.readouterr().out


def test_the_sensitivity_table_is_shown_unless_turns_was_pinned(history, capsys):
    write, tmp_path = history
    write([read(12_144)])
    main([str(tmp_path)])
    # The full phrase, since pytest's own temp directory is named after this test
    # and the bare word appears in the path it prints.
    assert "sensitivity to how long" in capsys.readouterr().out

    main([str(tmp_path), "--turns", "12"])
    assert "sensitivity to how long" not in capsys.readouterr().out
