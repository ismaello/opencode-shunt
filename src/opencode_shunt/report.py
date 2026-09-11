#!/usr/bin/env python3
"""What the shunt saved in real sessions, measured from production instead of benchmarks.

bench.py answers the right question the wrong way: an A/B of scripted scenarios
costs real money, takes forty minutes, and the control arm swung by 146% because
the model picks a different strategy every run. No affordable number of repeats
resolves a change from that noise.

This reads what already exists. OpenCode records tokens and cost per session in
its own database; the shunt records, per operation, the content that would have
been ingested and what was returned instead. The session ids match, so the two
join and the counterfactual can be reconstructed:

    what the session would have ingested = what it did ingest + what was avoided

No extra API calls, no scripted tasks, and the sample grows by itself with use.

About cost, which is where this differs from the naive estimate: nearly all of a
real session's input arrives as cache reads, not fresh input, because the whole
conversation is re-sent every turn and Anthropic bills a cached token at about a
tenth. So content the shunt kept out was never going to cost full input price on
every turn - it would have cost full price once and cache price thereafter. That
is why our earlier runs showed 43% fewer tokens but only 9% less money. The
saving is reported as a range for that reason.

Usage:
    ./session-report.py                 every session with shunt activity
    ./session-report.py --model opus    only sessions orchestrated by Opus
    ./session-report.py --days 7
"""

import argparse
import json
import pathlib
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone

from . import paths

DB = paths.opencode_db()
TELEMETRY = paths.telemetry_file()

# List prices per million tokens. Only used to turn token counts into a familiar
# unit; every percentage in this report is computed from tokens, not from these.
PRICES = {
    "claude-opus": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
    "claude-sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3},
    "gemini-2.5-flash": {"input": 0.3, "output": 2.5, "cache_read": 0.075},
    "gemini-2.5-flash-lite": {"input": 0.1, "output": 0.4, "cache_read": 0.025},
}


def prices_for(model: str) -> dict:
    for key, value in PRICES.items():
        if key in model:
            return value
    return PRICES["claude-opus"]


def load_sessions() -> dict[str, dict]:
    if not DB.exists():
        sys.exit(f"no OpenCode database at {DB}")
    # Read-only: the database is live and has WAL files beside it.
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    sessions = {}
    for row in con.execute(
        "SELECT s.id, s.model, s.agent, s.title, s.cost, s.tokens_input, s.tokens_output,"
        " s.tokens_cache_read, s.tokens_cache_write, s.time_created,"
        " (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id)"
        " FROM session s"
    ):
        model = row[1] or ""
        # The column holds a JSON blob; the readable name is inside it.
        if model.startswith("{"):
            try:
                model = json.loads(model).get("id", model)
            except json.JSONDecodeError:
                pass
        sessions[row[0]] = {
            "model": model,
            "agent": row[2] or "?",
            "title": (row[3] or "")[:60],
            "cost": row[4] or 0.0,
            "input": row[5] or 0,
            "output": row[6] or 0,
            "cache_read": row[7] or 0,
            "cache_write": row[8] or 0,
            "created": row[9] or 0,
            "turns": row[10] or 0,
        }
    return sessions


def load_operations() -> tuple[dict[str, list[dict]], float]:
    """Shunt operations grouped by session, plus a measured chars-per-token ratio."""
    if not TELEMETRY.exists():
        sys.exit(f"no telemetry at {TELEMETRY}")
    rows = []
    for line in TELEMETRY.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Calibrate against the workers' own token counts rather than guessing: for a
    # bulk_read we know the characters sent and the tokens they became.
    #
    # Two corrections matter. The worker sees each line prefixed with a line-number
    # gutter of about seven characters, which is tokenised but is not part of
    # content_chars; and the prompt carries the system instructions and question.
    # Leaving either out drove the measured ratio down to 2.34, well below the ~3.2
    # that code actually tokenises at, which would have overstated every saving.
    ratios = [
        (r["content_chars"] + r.get("lines", 0) * 7) / (r["local_prompt_tokens"] - 400)
        for r in rows
        if r.get("content_chars") and r.get("local_prompt_tokens", 0) > 2000
    ]
    ratio = statistics.median(ratios) if ratios else 3.3

    by_session: dict[str, list[dict]] = {}
    for r in rows:
        sid = r.get("sessionID")
        if not sid:
            continue
        # Each pair is (chars that would have been ingested, chars ingested instead).
        if r.get("tool") == "bulk_read" and r.get("content_chars"):
            pair = (r["content_chars"], r["returned_chars"], "read")
        elif r.get("event") == "output-shunt" and r.get("raw_bytes"):
            pair = (r["raw_bytes"], r["summary_bytes"], "output")
        elif r.get("tool") == "delegate_write" and r.get("written_chars"):
            pair = (r["written_chars"], r["returned_chars"], "write")
        else:
            continue
        by_session.setdefault(sid, []).append(
            {"before": pair[0], "after": pair[1], "kind": pair[2]}
        )
    return by_session, ratio


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", nargs="?", default=None, help="repository to report on")
    parser.add_argument("--model", help="substring of the orchestrator model, e.g. opus")
    parser.add_argument("--days", type=int, help="only sessions started in the last N days")
    # The CLI has always advertised --since and this has always accepted only
    # --days, so `shunt report --since` exited with a usage error.
    parser.add_argument("--since", help="ISO date, e.g. 2026-09-01")
    parser.add_argument("--all-repos", action="store_true", help="every repository, not just this one")
    parser.add_argument("--detail", action="store_true", help="one line per session")
    parser.add_argument(
        "--chars-per-token",
        type=float,
        help="override the measured ratio, to test how much the estimate depends on it",
    )
    args = parser.parse_args(argv)

    sessions = load_sessions()
    operations, measured = load_operations()
    ratio = args.chars_per_token or measured

    cutoff = 0
    if args.days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000
    if args.since:
        try:
            when = datetime.fromisoformat(args.since)
        except ValueError:
            return print(f"--since wants an ISO date like 2026-09-01, not {args.since!r}") or 2
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        cutoff = max(cutoff, when.timestamp() * 1000)

    # Sessions from every repository share one database, so without this the
    # report credits work done elsewhere to wherever you happen to be standing.
    mine = None
    if not args.all_repos:
        mine = paths.sessions_in(paths.resolve_repo(args.repo))

    rows = []
    for sid, ops in operations.items():
        session = sessions.get(sid)
        if not session:
            continue
        if mine is not None and sid not in mine:
            continue
        if args.model and args.model.lower() not in session["model"].lower():
            continue
        if cutoff and session["created"] < cutoff:
            continue

        ingested = session["input"] + session["cache_read"] + session["cache_write"]
        if ingested == 0:
            continue  # Never reached the model.

        avoided_chars = sum(op["before"] - op["after"] for op in ops)
        avoided = avoided_chars / ratio
        written = sum(op["before"] - op["after"] for op in ops if op["kind"] == "write") / ratio

        rows.append({**session, "id": sid, "ops": len(ops), "ingested": ingested,
                     "avoided": avoided, "avoided_output": written})

    if not rows:
        print("no sessions matched. Use the shunt for a while and run this again.")
        return 0

    print(f"calibración: {ratio:.2f} caracteres por token, medida sobre las llamadas reales\n")

    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_model.setdefault(row["model"], []).append(row)

    print("=== Ahorro estimado de contexto, por modelo orquestador ===\n")
    print(f"{'modelo':<24}{'ses':>5}{'ops':>5}{'ingerido':>12}{'evitado':>11}{'ahorro':>9}")
    for model, group in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        ingested = sum(r["ingested"] for r in group)
        avoided = sum(r["avoided"] for r in group)
        counterfactual = ingested + avoided
        print(
            f"{model[:23]:<24}{len(group):>5}{sum(r['ops'] for r in group):>5}"
            f"{ingested:>12,.0f}{avoided:>11,.0f}{100 * avoided / counterfactual:>8.1f}%"
        )

    print("\n=== Coste ===\n")
    for model, group in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        price = prices_for(model)
        spent = sum(r["cost"] for r in group)
        avoided_in = sum(r["avoided"] - r["avoided_output"] for r in group)
        avoided_out = sum(r["avoided_output"] for r in group)
        turns = statistics.median([max(r["turns"], 1) for r in group])

        # Lower bound: the content would have been ingested once at full price.
        # Upper bound: plus a cache read on each remaining turn of the session,
        # which is what actually happens to anything left in the context.
        low = avoided_in / 1e6 * price["input"] + avoided_out / 1e6 * price["output"]
        high = low + avoided_in / 1e6 * price["cache_read"] * max(turns - 1, 0)
        print(
            f"{model[:23]:<24} gastado ${spent:>7.2f}   evitado ${low:.2f} a ${high:.2f}"
            f"   ({100 * low / (spent + low):.0f}% a {100 * high / (spent + high):.0f}% de la factura)"
        )

    print(
        "\nOJO con la cifra de coste: sale más alta que el 28% que dio el A/B real, y el"
        "\nA/B es medición directa de dos brazos, no una estimación. La diferencia es la"
        "\nlimitación 1 de abajo: aquí se cobra a precio de entrada todo lo evitado, pero"
        "\nsin shunt el modelo habría leído menos de eso. Fíate del A/B para el valor"
        "\nabsoluto y de esto para ver la tendencia con el uso real."
    )

    if args.detail:
        print("\n=== Por sesión ===\n")
        print(f"{'ops':>4}{'ingerido':>11}{'evitado':>10}{'turnos':>8}  titulo")
        for row in sorted(rows, key=lambda r: -r["avoided"]):
            print(
                f"{row['ops']:>4}{row['ingested']:>11,}{row['avoided']:>10,.0f}"
                f"{row['turns']:>8}  {row['title']}"
            )

    print(
        "\nLIMITACIONES, que importan para interpretar lo anterior:"
        "\n"
        "\n1. Esto mide contenido que no entró al contexto, no lecturas que el modelo"
        "\n   hubiera hecho con certeza. Sin shunt puede que hubiera usado grep, o leído"
        "\n   solo parte. El A/B mostró justo eso, así que la cifra es un techo."
        "\n2. La ratio de caracteres por token está calibrada con el tokenizador del"
        "\n   worker, no con el de Claude. Si Claude tokeniza más grueso, el ahorro real"
        "\n   es menor. Usa --chars-per-token 3.5 para ver cuánto depende de esto."
        "\n3. El coste es un rango porque depende de cuántos turnos habría sobrevivido en"
        "\n   contexto lo evitado, y un token en caché cuesta una décima parte."
        f"\n4. Muestra pequeña: {sum(r['ops'] for r in rows)} operaciones en {len(rows)} sesiones."
    )
    return 0

