#!/usr/bin/env python3
"""A/B benchmark for the local shunt.

Runs the same question twice against the same repository: once with the shunt
disabled (the frontier model reads files itself) and once with it enforced
(reading is delegated to the local GPU model), then compares cloud token usage.

Usage:
    ./bench.py --repo /path/to/repo --name test-a "Your question here"
    ./bench.py --repo /path/to/repo --report        # summarise past runs
"""

import argparse
import json
import os
import pathlib
import shlex
import statistics
import subprocess
import sys
import time

RESULTS = pathlib.Path.home() / ".local/share/opencode-shunt/benchmarks.jsonl"
MODEL = os.environ.get("BENCH_MODEL", "anthropic/claude-opus-4-8")


BASELINE_AGENT = os.environ.get("BENCH_BASELINE_AGENT", "benchmark-baseline")
SHUNT_AGENT = os.environ.get("BENCH_SHUNT_AGENT", "orchestrator")


def run_once(repo: str, question: str, enforce: bool) -> dict:
    """Run one question through opencode and total up what the cloud model spent."""
    env = {
        **os.environ,
        "SHUNT_MODE": "enforce" if enforce else "observe",
        # A fresh session each time, so cache reads do not leak between arms.
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
    }
    # opencode fails to start when exec'd directly from a list argv; going through
    # a shell works. Not worth chasing further, so quote carefully and use one.
    command = shlex.join(
        [
            "opencode", "run",
            "--agent", SHUNT_AGENT if enforce else BASELINE_AGENT,
            "--model", MODEL,
            "--format", "json",
            "--auto",
            question,
        ]
    )
    started = time.time()
    proc = subprocess.run(
        command,
        cwd=repo,
        env=env,
        shell=True,
        executable="/bin/bash",
        capture_output=True,
        text=True,
        timeout=1800,
    )

    totals = {"input": 0, "output": 0, "reasoning": 0, "cache_write": 0, "cache_read": 0}
    cost = 0.0
    steps = 0
    tools: list[str] = []
    answer = ""

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part", {})
        if event.get("type") == "step_finish":
            tok = part.get("tokens", {})
            totals["input"] += tok.get("input", 0)
            totals["output"] += tok.get("output", 0)
            totals["reasoning"] += tok.get("reasoning", 0)
            totals["cache_write"] += tok.get("cache", {}).get("write", 0)
            totals["cache_read"] += tok.get("cache", {}).get("read", 0)
            cost += part.get("cost", 0.0)
            steps += 1
        if part.get("type") == "tool" and part.get("tool"):
            tools.append(part["tool"])
        if part.get("type") == "text" and part.get("text"):
            answer = part["text"]

    # Everything the model had to ingest, however it was billed.
    ingested = totals["input"] + totals["cache_write"] + totals["cache_read"]

    return {
        "arm": "shunt" if enforce else "baseline",
        "agent": SHUNT_AGENT if enforce else BASELINE_AGENT,
        "model": MODEL,
        "tokens": totals,
        "ingested_tokens": ingested,
        "cost_usd": round(cost, 4),
        "steps": steps,
        "tools": sorted(set(tools)),
        "wall_s": round(time.time() - started, 1),
        "answer": answer.strip(),
        "returncode": proc.returncode,
        "stderr": proc.stderr[-500:],
        "stdout_tail": proc.stdout[-500:] if not steps else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", help="question to benchmark")
    parser.add_argument("--repo", required=False, help="repository to run in")
    parser.add_argument("--name", default="unnamed", help="label for this benchmark")
    parser.add_argument("--report", action="store_true", help="summarise past runs")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="runs per arm; the control arm varies by ~25%%, so use 3 or more to conclude anything",
    )
    args = parser.parse_args()

    if args.report:
        if not RESULTS.exists():
            print("no benchmarks recorded yet")
            return 1
        rows = [json.loads(line) for line in RESULTS.read_text().splitlines() if line]
        # Runs that never reached the model produce no tokens and would skew the table.
        rows = [row for row in rows if row["ingested_tokens"] > 0]
        if not rows:
            print("no successful benchmark runs recorded")
            return 1

        width = max(len(row["name"]) for row in rows) + 2

        # Repeated runs of the same arm vary by tens of percent, because the model
        # picks a different strategy each time. Reporting one run of each invites
        # reading noise as signal, so aggregate and show the spread.
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            groups.setdefault((row["name"], row["arm"]), []).append(row)

        print(
            f"{'name':<{width}}{'arm':<10}{'n':>3}{'ingested med':>14}{'min':>9}{'max':>9}"
            f"{'output':>8}{'cost $':>9}  tools seen"
        )
        for (name, arm), runs in groups.items():
            ingested = [r["ingested_tokens"] for r in runs]
            tools = sorted({t for r in runs for t in r["tools"]})
            print(
                f"{name:<{width}}{arm:<10}{len(runs):>3}"
                f"{int(statistics.median(ingested)):>14,}{min(ingested):>9,}{max(ingested):>9,}"
                f"{int(statistics.median([r['tokens']['output'] for r in runs])):>8,}"
                f"{statistics.median([r['cost_usd'] for r in runs]):>9.4f}"
                f"  {','.join(tools) or '-'}"
            )

        def median_of(name: str, arm: str, field) -> float | None:
            runs = groups.get((name, arm))
            return statistics.median([field(r) for r in runs]) if runs else None

        names = sorted({name for name, _ in groups})
        print(f"\n{'benchmark':<{width}}{'n':>4}{'ahorro tokens':>15}{'ahorro coste':>14}  fiabilidad")
        total_saved = total_base = 0.0
        for name in names:
            base = median_of(name, "baseline", lambda r: r["ingested_tokens"])
            shunt = median_of(name, "shunt", lambda r: r["ingested_tokens"])
            if base is None or shunt is None:
                continue
            base_cost = median_of(name, "baseline", lambda r: r["cost_usd"])
            shunt_cost = median_of(name, "shunt", lambda r: r["cost_usd"])
            n = min(len(groups[(name, "baseline")]), len(groups[(name, "shunt")]))
            total_saved += base - shunt
            total_base += base

            # Spread of the control arm bounds what a single run can resolve.
            control = [r["ingested_tokens"] for r in groups[(name, "baseline")]]
            spread = 100 * (max(control) - min(control)) / statistics.median(control)
            verdict = "solido" if n >= 3 and spread < 20 else f"n={n}, control +-{spread:.0f}%"
            print(
                f"{name:<{width}}{n:>4}{100 * (base - shunt) / base:>14.1f}%"
                f"{100 * (base_cost - shunt_cost) / base_cost:>13.1f}%  {verdict}"
            )
        if total_base:
            print(f"{'TOTAL':<{width}}{'':>4}{100 * total_saved / total_base:>14.1f}%")
        return 0

    if not args.question or not args.repo:
        parser.error("question and --repo are required unless --report is used")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    for round_number in range(1, args.repeat + 1):
        for enforce in (False, True):
            arm = "shunt" if enforce else "baseline"
            print(f"--- {arm} run {round_number}/{args.repeat} ---", file=sys.stderr)
            result = run_once(args.repo, args.question, enforce)
            result = {
                "ts": time.strftime("%FT%T"),
                "name": args.name,
                "question": args.question,
                "round": round_number,
                **result,
            }
            with RESULTS.open("a") as handle:
                handle.write(json.dumps(result) + "\n")
            print(
                f"{arm:<9} ingested={result['ingested_tokens']:<8} "
                f"output={result['tokens']['output']:<6} cost=${result['cost_usd']:<8} "
                f"tools={','.join(result['tools']) or 'none'}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
