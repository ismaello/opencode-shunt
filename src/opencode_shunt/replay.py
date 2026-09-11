#!/usr/bin/env python3
"""What a different threshold would have done to work you already did.

This exists because of a measurement that nearly cost real money. The read shunt
blocks at a hand-written 400 lines while `bulk_read` refuses below an
economically computed floor, and on this project those two numbers differ by
four times. Unifying them looked obviously right, and the obvious way to check
was an A/B of two arms on a real task.

The A/B would have been useless. `bench.py` already documents why: the control
arm swung by 146% between runs, because the model picks a different strategy
every time, and no affordable number of repeats resolves a small change from
that noise. Replaying the history instead cost nothing, took a second, and gave
an answer with no variance at all: the change would have touched 5 of 81 reads
and been worth 8% of one session. Below the noise floor, so not worth the risk
of hardening a guard whose last hardening cost 84%.

That is the shape of the tool. It does not tell you what a model would do
differently - only what was already recorded, re-judged. Which is less than an
A/B measures, and enormously more than an A/B can resolve when the effect is
small.

    shunt replay                  the shipped fixed floor against the economic one
    shunt replay --floor 20000    a specific byte threshold
    shunt replay --turns 12       sensitivity to how long content lives in context
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
from dataclasses import replace

from . import paths
from .economics import Economics, assess

# The shipped read thresholds, from runtime/plugins/shunt.ts. Duplicated here on
# purpose: the point is to compare against what actually ran.
FIXED_MAX_BYTES = 40_960
FIXED_MAX_LINES = 400

# The variance of a session-level A/B on this project, measured. Any modelled
# effect below this cannot be confirmed by running the task, which is the single
# most useful thing this command can tell you.
NOISE_FLOOR_PERCENT = 146.0


def load_reads(cutoff_repo: pathlib.Path | None) -> tuple[list[dict], dict[str, str], dict[str, float]]:
    """Reads the shunt allowed for being small, with their session and repository."""
    database = paths.opencode_db()
    directories: dict[str, str] = {}
    costs: dict[str, float] = {}
    if database.is_file():
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        for sid, directory, cost in connection.execute("SELECT id, directory, cost FROM session"):
            directories[sid] = directory or ""
            costs[sid] = cost or 0.0

    telemetry = paths.telemetry_file()
    if not telemetry.is_file():
        return [], directories, costs

    reads = []
    for line in telemetry.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("tool") != "read" or not row.get("bytes"):
            continue
        # Only the ones that were let through for being small can change verdict
        # under a lower floor. A block stays a block.
        if row.get("verdict") not in ("allow-small",):
            continue
        if cutoff_repo is not None and directories.get(row.get("sessionID", ""), "") != str(cutoff_repo):
            continue
        reads.append(row)
    return reads, directories, costs


def economics_for(repo: str) -> Economics:
    """A repository's own economics, since the floor is priced per orchestrator."""
    try:
        config = json.loads((pathlib.Path(repo) / ".opencode" / "shunt.json").read_text())
    except Exception:
        return Economics()
    return Economics.from_config(config.get("economics"))


def in_band(read: dict, floor: int) -> bool:
    """Allowed by the fixed thresholds, but above the floor being tested."""
    if read["bytes"] > FIXED_MAX_BYTES or read.get("lines", 0) > FIXED_MAX_LINES:
        return False  # The fixed floor already caught it.
    return read["bytes"] >= floor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--all-repos", action="store_true", help="every repository, not just this one")
    parser.add_argument("--floor", type=int, help="byte threshold to test, instead of the economic one")
    parser.add_argument("--turns", type=int, help="assume content survives this many turns in context")
    args = parser.parse_args(argv)

    repo = paths.resolve_repo(args.repo)
    reads, directories, costs = load_reads(None if args.all_repos else repo)

    if not reads:
        scope = "any repository" if args.all_repos else str(repo)
        print(f"no reads recorded for {scope}. Use the shunt for a while and run this again.")
        return 0

    by_repo: dict[str, list[dict]] = {}
    for read in reads:
        by_repo.setdefault(directories.get(read.get("sessionID", ""), "?"), []).append(read)

    print(f"replaying {len(reads)} read(s) the shunt allowed for being small")
    print(f"against the shipped floor of {FIXED_MAX_LINES} lines / {FIXED_MAX_BYTES:,} bytes\n")

    print(f"{'repositorio':<34}{'suelo':>9}{'en banda':>10}{'bytes':>12}{'neto':>10}")
    total_net = 0.0
    total_band: list[dict] = []
    affected: set[str] = set()

    for directory, group in sorted(by_repo.items(), key=lambda kv: -len(kv[1])):
        economics = economics_for(directory)
        if args.turns:
            economics = replace(economics, remaining_turns=args.turns)
        floor = args.floor if args.floor else assess(0, economics).break_even_chars

        band = [read for read in group if in_band(read, floor)]
        chars = sum(read["bytes"] for read in band)
        # One delegation per session, not per file: that is how bulk_read is
        # called, and charging the extra round trips once per file would make
        # any change look worse than it is.
        sessions = {read.get("sessionID") for read in band}
        net = sum(
            assess(sum(r["bytes"] for r in band if r.get("sessionID") == sid), economics).net
            for sid in sessions
        )
        total_net += net
        total_band += band
        affected |= sessions

        name = pathlib.Path(directory).name or directory or "?"
        print(f"{name[:33]:<34}{floor:>9,}{len(band):>10}{chars:>12,}{net:>9.4f}$")

    share = 100 * len(total_band) / len(reads)
    print(f"\nwould change verdict on {len(total_band)} of {len(reads)} reads ({share:.0f}%), "
          f"across {len(affected)} session(s)")
    print(f"modelled saving: ${total_net:.4f}")

    spent = sum(costs.get(sid, 0.0) for sid in affected)
    if spent:
        effect = 100 * total_net / spent
        print(f"those sessions cost ${spent:.4f}, so the effect is {effect:.1f}% of them\n")
        # The point of the whole command. An effect under the measured variance
        # of a two-arm run cannot be confirmed by doing the run, so saying
        # "inconclusive" here is worth more than the estimate above it.
        if effect < NOISE_FLOOR_PERCENT:
            print(
                f"NOT WORTH AN A/B. A two-arm run on this project varied by {NOISE_FLOOR_PERCENT:.0f}%\n"
                f"between executions of the identical task, so a {effect:.1f}% effect would come back\n"
                "indistinguishable from noise, having cost real money to produce.\n"
                "\n"
                "Weigh it against the downside instead, which is measured rather than modelled:\n"
                "hardening the read guard once turned a $0.25 task into $0.47, because every\n"
                "refusal costs the turn the model spends being told no."
            )
        else:
            print(
                f"An A/B could resolve this: {effect:.1f}% is above the {NOISE_FLOOR_PERCENT:.0f}% variance\n"
                "of a two-arm run. Use 'shunt bench' and expect it to cost money."
            )

    if not args.turns:
        print("\nsensitivity to how long content lives in context, which dominates the answer:")
        print(f"  {'turnos':<9}{'suelo':>9}{'en banda':>10}{'neto':>10}")
        for turns in (2, 3, 5, 8, 12, 20):
            net = 0.0
            band_count = 0
            floor_shown = 0
            for directory, group in by_repo.items():
                economics = replace(economics_for(directory), remaining_turns=turns)
                floor = args.floor if args.floor else assess(0, economics).break_even_chars
                floor_shown = floor
                band = [read for read in group if in_band(read, floor)]
                band_count += len(band)
                for sid in {read.get("sessionID") for read in band}:
                    net += assess(sum(r["bytes"] for r in band if r.get("sessionID") == sid), economics).net
            print(f"  {turns:<9}{floor_shown:>9,}{band_count:>10}{net:>9.4f}$")
        print(
            "\nRun 'shunt report' to see what your own median actually is. Mostly one-shot\n"
            "'opencode run' sessions sit at 2; long interactive work runs far higher, and the\n"
            "same change can be worth attacking there and not here."
        )

    print(
        "\nWhat this does not tell you: whether the model would have delegated after being\n"
        "refused, or how many turns that took. It re-judges what was recorded. The saving\n"
        "above is a ceiling on a decision, not a measurement of one."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
