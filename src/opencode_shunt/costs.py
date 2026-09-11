#!/usr/bin/env python3
"""Write down what this repository has cost, in a file a person can read.

`stats` and `report` answer "did the shunt work". Neither answers the question
somebody actually asks at the end of a month, which is "what did this cost me,
and where did it go". That question has an awkward property: the answer lives in
two places that never meet. OpenCode's database knows what was billed; the
shunt's telemetry knows what was avoided. Only together do they say whether the
bill is the one you should have had.

So this joins them and writes Markdown, because the output is meant to be kept,
attached to a message, or read next month when nobody remembers the run. The one
number worth trusting absolutely is the spend: it comes from OpenCode's own
accounting, not from a token estimate of ours. Everything about what was avoided
is an estimate, and says so where it appears.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

from . import paths, providers

# Where an unpriced model lands. Chosen high on purpose: an underestimate of
# spend is the misleading direction, since it makes the shunt look unnecessary.
FALLBACK_PRICES = {"price_in": 5.0, "price_out": 25.0, "cache_write": 6.25, "cache_read": 0.5}


def model_name(raw: str | None) -> str:
    """OpenCode stores this as either a bare name or a JSON blob."""
    name = raw or "desconocido"
    if name.startswith("{"):
        try:
            return json.loads(name).get("id", "desconocido")
        except json.JSONDecodeError:
            return "desconocido"
    return name


def load_sessions(repo: pathlib.Path | None, cutoff_ms: float) -> list[dict]:
    database = paths.opencode_db()
    if not database.is_file():
        return []

    # Read-only: the database is live and has WAL files beside it.
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    rows = []
    for row in connection.execute(
        "SELECT id, model, agent, title, directory, cost, tokens_input, tokens_output,"
        " tokens_cache_read, tokens_cache_write, time_created FROM session"
    ):
        created = row[10] or 0
        if cutoff_ms and created < cutoff_ms:
            continue
        # Sessions are recorded against the directory they ran in, which is what
        # makes a per-repository bill possible at all.
        if repo is not None and row[4] != str(repo):
            continue
        rows.append(
            {
                "id": row[0],
                "model": model_name(row[1]),
                "agent": row[2] or "?",
                "title": (row[3] or "").strip() or "(sin título)",
                "cost": row[5] or 0.0,
                "input": row[6] or 0,
                "output": row[7] or 0,
                "cache_read": row[8] or 0,
                "cache_write": row[9] or 0,
                "created": created,
            }
        )
    return sorted(rows, key=lambda r: r["created"])


def load_telemetry(cutoff_ms: float) -> list[dict]:
    telemetry = paths.telemetry_file()
    if not telemetry.is_file():
        return []
    cutoff = datetime.fromtimestamp(cutoff_ms / 1000, timezone.utc).isoformat() if cutoff_ms else ""
    rows = []
    for line in telemetry.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff and entry.get("ts", "") < cutoff:
            continue
        rows.append(entry)
    return rows


def split_spend(session: dict) -> dict[str, float]:
    """Where one session's money went, at list prices.

    OpenCode records a single cost figure, which is the one to trust, but not
    what it was spent on. Splitting it by token class is what turns the bill
    into a decision: output is attacked by delegating writes, cache writes by
    delegating reads, and cache reads only by having fewer turns.
    """
    prices = providers.prices_for_model(session["model"]) or FALLBACK_PRICES
    per = lambda tokens, price: (tokens / 1_000_000) * (price or 0.0)
    return {
        "entrada": per(session["input"], prices["price_in"]),
        "salida": per(session["output"], prices["price_out"]),
        "cache write": per(session["cache_write"], prices["cache_write"]),
        "cache read": per(session["cache_read"], prices["cache_read"]),
    }


def worker_spend(rows: list[dict], profile_model: str | None = None) -> tuple[float, int, int]:
    """What the workers themselves cost, which OpenCode never sees.

    Worker calls are direct HTTP from our own tools, so they create no OpenCode
    session and land in no OpenCode invoice. Reporting only what the database
    knows understates the real bill, and understates it in the flattering
    direction: the whole argument for this system is that the cheap side is
    nearly free, so the cheap side is exactly where an omission must not sit.

    Measured once this was counted properly: 1.9% and 2.2% of two real
    repositories, against the 0.8% that counting only sessions suggested.
    """
    total = 0.0
    calls = 0
    unpriced = 0
    cache: dict[str, dict | None] = {}

    for row in rows:
        prompt = row.get("local_prompt_tokens") or 0
        if not prompt:
            continue
        calls += 1
        completion = row.get("local_completion_tokens") or row.get("local_output_tokens") or 0

        # Each call records the model that served it, so each call is priced
        # with that model. Applying one rate to every row put reader and writer
        # on the same tariff, and where those differ the error is not small:
        # with a cheap reader and an expensive writer, work worth $10.30 was
        # reported as $0.60.
        model = row.get("worker_model") or profile_model or ""
        if model not in cache:
            cache[model] = providers.prices_for_model(model) if model else None
        prices = cache[model]

        # A model we cannot price is reported as unpriced. Substituting a
        # plausible default produces a total that looks authoritative and is
        # not, which is worse than an admitted gap.
        if prices is None or prices.get("price_in") is None:
            unpriced += 1
            continue

        # `or` was used here, which turned a genuine zero into a fallback rate:
        # a worker running locally for free was billed at Gemini Flash prices,
        # in the one configuration where the answer is certain.
        price_in = prices["price_in"]
        price_out = prices["price_out"] if prices.get("price_out") is not None else 0.0
        total += prompt / 1e6 * price_in + completion / 1e6 * price_out

    return total, calls, unpriced


def savings(rows: list[dict]) -> list[dict]:
    """What each kind of delegation kept out, in characters."""
    kinds = [
        ("bulk_read", "lectura delegada", "content_chars", "returned_chars"),
        ("output shunt", "salida de comandos resumida", "raw_bytes", "summary_bytes"),
        ("delegate_write", "ficheros generados", "written_chars", "returned_chars"),
        ("delegate_edit", "ediciones delegadas", "written_chars", "returned_chars"),
    ]
    out = []
    for key, label, before_field, after_field in kinds:
        if key == "output shunt":
            matching = [r for r in rows if r.get("event") == "output-shunt"]
        else:
            matching = [r for r in rows if r.get("tool") == key and not r.get("event")]
            # The edit metric was recomputed once; older rows are not comparable.
            if key == "delegate_edit":
                matching = [r for r in matching if r.get("metric_version", 0) >= 2]
        pairs = [
            (r[before_field], r[after_field])
            for r in matching
            if r.get(before_field) and r.get(after_field) is not None
        ]
        if not pairs:
            continue
        before = sum(p[0] for p in pairs)
        after = sum(p[1] for p in pairs)
        out.append(
            {
                "label": label,
                "ops": len(pairs),
                "before": before,
                "after": after,
                "saved": before - after,
                # Output tokens cost five times input, so the two cannot be added.
                "is_output": key in ("delegate_write", "delegate_edit"),
            }
        )
    return out


def money(value: float) -> str:
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def render(
    sessions: list[dict],
    telemetry: list[dict],
    scope: str,
    days: int | None,
    worker_model: str | None = None,
) -> str:
    total = sum(s["cost"] for s in sessions)
    when = datetime.now().strftime("%d/%m/%Y %H:%M")
    period = f"últimos {days} días" if days else "todo el histórico"

    lines = [
        "# Costes",
        "",
        f"**{scope}** · {period} · generado el {when} por `shunt costs`",
        "",
    ]

    if not sessions:
        lines += [
            "No hay ninguna sesión registrada para este ámbito. Si esperabas verlas,",
            "comprueba que estás en el repositorio correcto: las sesiones se guardan",
            "con el directorio donde se lanzaron.",
            "",
        ]
        return "\n".join(lines)

    tokens_out = sum(s["output"] for s in sessions)
    worker_cost, worker_calls, unpriced = worker_spend(telemetry, worker_model)
    lines += [
        "## Resumen",
        "",
        f"- **Gasto total: {money(total + worker_cost)}** en {len(sessions)} sesiones",
        f"  - orquestador: {money(total)}",
        f"  - workers: {money(worker_cost)} en {worker_calls} llamadas"
        + (f" ({100 * worker_cost / (total + worker_cost):.1f}%)" if total + worker_cost else ""),
        f"- {tokens_out:,} tokens de salida escritos por el modelo caro",
        f"- Coste medio por sesión: {money((total + worker_cost) / len(sessions))}",
        "",
        "El gasto del orquestador sale de la contabilidad de OpenCode. El de los",
        "workers no puede salir de ahí: son llamadas HTTP directas que no crean sesión,",
        "así que se calcula con los tokens que registra nuestra propia telemetría,",
        "cada llamada al precio del modelo que la atendió. Todo",
        "lo que viene después sobre ahorro sí es estimado.",
        "",
    ]
    if unpriced:
        lines += [
            f"> **{unpriced} de {worker_calls} llamadas al worker no se han podido tarifar**",
            "> porque no conocemos el precio de su modelo, así que quedan fuera del total.",
            "> El gasto real de workers es algo mayor que el que aparece arriba.",
            "",
        ]

    # --- per model -----------------------------------------------------------
    by_model: dict[str, list[dict]] = {}
    for session in sessions:
        by_model.setdefault(session["model"], []).append(session)

    lines += [
        "## Por modelo",
        "",
        "| Modelo | Sesiones | Gasto | % | Salida | Cache write | Cache read |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model, group in sorted(by_model.items(), key=lambda kv: -sum(s["cost"] for s in kv[1])):
        spent = sum(s["cost"] for s in group)
        share = 100 * spent / total if total else 0
        lines.append(
            f"| `{model}` | {len(group)} | {money(spent)} | {share:.0f}% |"
            f" {sum(s['output'] for s in group):,} |"
            f" {sum(s['cache_write'] for s in group):,} | {sum(s['cache_read'] for s in group):,} |"
        )
    lines.append("")

    # --- where the money goes ------------------------------------------------
    concepts: dict[str, float] = {}
    for session in sessions:
        for concept, value in split_spend(session).items():
            concepts[concept] = concepts.get(concept, 0.0) + value
    estimated = sum(concepts.values())

    lines += [
        "## En qué se va",
        "",
        "| Concepto | Coste | % | Qué lo reduce |",
        "| --- | ---: | ---: | --- |",
    ]
    what_helps = {
        "salida": "`delegate_write` y `delegate_edit`",
        "cache write": "`bulk_read` y el resumen de salidas",
        "cache read": "sesiones más cortas; nada más lo toca",
        "entrada": "poco: casi todo entra ya como cache write",
    }
    for concept, value in sorted(concepts.items(), key=lambda kv: -kv[1]):
        share = 100 * value / estimated if estimated else 0
        lines.append(f"| {concept} | {money(value)} | {share:.0f}% | {what_helps[concept]} |")
    lines += [
        "",
        f"Este desglose reparte {money(estimated)} a precio de lista, contra los"
        f" {money(total)} realmente facturados. Si no cuadran, es que algún modelo"
        " no está en el catálogo de precios o hubo descuentos.",
        "",
    ]

    # --- what the shunt avoided ---------------------------------------------
    avoided = savings(telemetry)
    if avoided:
        lines += [
            "## Lo que no llegó al modelo caro",
            "",
            "| Operación | Veces | Habría entrado | Entró | Ahorro |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for row in avoided:
            share = 100 * row["saved"] / row["before"] if row["before"] else 0
            lines.append(
                f"| {row['label']} | {row['ops']} | {row['before'] / 1024:,.0f} KB |"
                f" {row['after'] / 1024:,.0f} KB | {share:.0f}% |"
            )

        # Kept apart because output bills at about five times input, so adding
        # the two would understate what the write tools are worth.
        chars_in = sum(r["saved"] for r in avoided if not r["is_output"])
        chars_out = sum(r["saved"] for r in avoided if r["is_output"])
        prices = providers.prices_for_model(max(by_model, key=lambda m: len(by_model[m]))) or FALLBACK_PRICES
        value_in = (chars_in / 2.9 / 1e6) * (prices["cache_write"] or 0)
        value_out = (chars_out / 2.9 / 1e6) * (prices["price_out"] or 0)
        lines += [
            "",
            f"A los precios del orquestador, eso vale del orden de"
            f" **{money(value_in + value_out)}**"
            f" ({money(value_in)} de entrada, {money(value_out)} de salida).",
            "",
            "Es un techo, no una factura evitada. Sin el sistema el modelo no habría",
            "leído necesariamente todo lo que se le ahorró: puede que hubiera hecho un",
            "grep, o leído solo una parte. Sirve para ver la tendencia, no para",
            "presumir de una cifra.",
            "",
        ]

    # --- the expensive ones --------------------------------------------------
    dearest = sorted(sessions, key=lambda s: -s["cost"])[:10]
    if len(sessions) > 1:
        lines += [
            "## Las sesiones más caras",
            "",
            "| Fecha | Coste | Salida | Modelo | Título |",
            "| --- | ---: | ---: | --- | --- |",
        ]
        for session in dearest:
            stamp = datetime.fromtimestamp(session["created"] / 1000).strftime("%d/%m %H:%M")
            title = session["title"].replace("|", "/")[:60]
            lines.append(
                f"| {stamp} | {money(session['cost'])} | {session['output']:,} |"
                f" `{session['model']}` | {title} |"
            )
        lines.append("")

    failures = [r for r in telemetry if r.get("event") in ("worker-failed", "output-shunt-failed")]
    if failures:
        lines += [
            "## Delegaciones que fallaron",
            "",
            f"{len(failures)} llamada(s) al worker no salieron, y cuando eso pasa el trabajo",
            "vuelve al modelo caro sin avisar. Merece la pena mirarlo:",
            "",
            f"- Último error: `{str(failures[-1].get('error', ''))[:160]}`",
            "",
            "Ejecuta `shunt doctor` para ver si es de configuración.",
            "",
        ]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--out", help="file to write (default: shunt-costes.md in the repository)")
    parser.add_argument("--days", type=int, help="only the last N days")
    parser.add_argument("--all-repos", action="store_true", help="every repository, not just this one")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing a file")
    args = parser.parse_args(argv)

    repo = paths.resolve_repo(args.repo)
    cutoff = 0.0
    if args.days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000

    scope = "todos los repositorios" if args.all_repos else f"`{repo}`"
    sessions = load_sessions(None if args.all_repos else repo, cutoff)
    telemetry = load_telemetry(cutoff)
    if not args.all_repos:
        # Telemetry has no directory, so scope it through the session ids.
        known = {s["id"] for s in sessions}
        telemetry = [r for r in telemetry if r.get("sessionID") in known]

    # The worker's own model, so its tokens are priced as what they were.
    worker_model = None
    try:
        config = json.loads((repo / ".opencode" / "shunt.json").read_text())
        worker_model = (config.get("_roles") or {}).get("reader", "").split("/")[-1] or None
    except Exception:
        pass

    document = render(sessions, telemetry, scope, args.days, worker_model)

    if args.stdout:
        print(document)
        return 0

    destination = pathlib.Path(args.out) if args.out else repo / "shunt-costes.md"
    destination.write_text(document + "\n", encoding="utf8")
    total = sum(s["cost"] for s in sessions)
    print(f"{destination}: {len(sessions)} sesiones, {money(total)}")
    if not sessions and not args.all_repos:
        print("Ninguna sesión en este repositorio. Prueba con --all-repos.")
    elif not args.out:
        # It lands in the repository root because the point is to share it, but
        # session titles are summaries of what you asked for, which is not
        # always something to commit.
        print("Contiene los títulos de las sesiones. Míralo antes de comitearlo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
