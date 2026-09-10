#!/usr/bin/env python3
"""Per-operation report on what the shunt actually saved.

The session-level A/B in bench.py measures the right thing but is a bad
instrument for iterating: one run takes minutes, costs real money, and the
control arm swings by up to 146% because the model picks a different strategy
every time. Nothing short of many repeats can resolve a change from that noise.

This measures each delegation instead: the characters the caller would have
ingested by reading the files, against the characters it ingested instead. Both
sides are text headed for the same model, so the ratio needs no assumptions
about tokenisers, and it comes free from telemetry already on disk.

Usage:
    ./shunt-stats.py              # everything recorded
    ./shunt-stats.py --since 2026-09-10
"""

import argparse
import json
import pathlib
import statistics
import sys

from . import paths

TELEMETRY = paths.telemetry_file()

# Rough, and only used to put the char counts in familiar units. The saving
# percentages never depend on it.
CHARS_PER_TOKEN = 3.5


def load(since: str | None) -> list[dict]:
    if not TELEMETRY.exists():
        sys.exit(f"no telemetry at {TELEMETRY}")
    rows = []
    for line in TELEMETRY.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and row.get("ts", "") < since:
            continue
        rows.append(row)
    return rows


def summarise(label: str, pairs: list[tuple[int, int]], width: int = 22) -> float | None:
    """pairs are (would_have_ingested, actually_ingested), in characters."""
    if not pairs:
        print(f"{label:<{width}} sin datos")
        return None
    savings = [100 * (a - b) / a for a, b in pairs if a > 0]
    total_before = sum(a for a, _ in pairs)
    total_after = sum(b for _, b in pairs)
    print(
        f"{label:<{width}}{len(pairs):>4} ops"
        f"{statistics.median(savings):>9.1f}% mediana"
        f"{min(savings):>8.1f}% peor"
        f"{max(savings):>8.1f}% mejor"
        f"   {total_before / 1024:>8.0f} KB -> {total_after / 1024:.0f} KB"
    )
    return 100 * (total_before - total_after) / total_before


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="ISO timestamp in UTC, e.g. 2026-09-10; telemetry is stamped in UTC")
    args = parser.parse_args(argv)
    rows = load(args.since)

    reads = [
        r
        for r in rows
        if r.get("tool") == "bulk_read" and r.get("content_chars") and r.get("returned_chars")
    ]
    outputs = [
        r for r in rows if r.get("event") == "output-shunt" and r.get("raw_bytes") and r.get("summary_bytes")
    ]
    writes = [
        r
        for r in rows
        if r.get("tool") == "delegate_write" and r.get("written_chars") and r.get("returned_chars")
    ]
    all_edits = [
        r
        for r in rows
        if r.get("tool") == "delegate_edit" and r.get("written_chars") and r.get("returned_chars")
    ]
    # The saving baseline changed once, so earlier rows would skew the median badly.
    edits = [r for r in all_edits if r.get("metric_version", 0) >= 2]
    stale_edits = len(all_edits) - len(edits)

    print("=== Ahorro por operacion (entrada) ===\n")
    summarise("bulk_read", [(r["content_chars"], r["returned_chars"]) for r in reads])
    summarise("output shunt", [(r["raw_bytes"], r["summary_bytes"]) for r in outputs])

    saved_in = sum(r["content_chars"] - r["returned_chars"] for r in reads)
    saved_in += sum(r["raw_bytes"] - r["summary_bytes"] for r in outputs)

    # Kept apart because these are output tokens, which bill at five times input
    # on Opus. Adding them to the input savings would understate what they are worth.
    print("\n=== Ahorro por operacion (salida) ===\n")
    summarise("delegate_write", [(r["written_chars"], r["returned_chars"]) for r in writes])
    # For an edit the baseline is not the file but the search-and-replace pairs the
    # orchestrator would have emitted: the changed lines plus their anchoring
    # context, twice over, at every site.
    summarise("delegate_edit", [(r["written_chars"], r["returned_chars"]) for r in edits])
    saved_out = sum(r["written_chars"] - r["returned_chars"] for r in writes)
    saved_out += sum(r["written_chars"] - r["returned_chars"] for r in edits)

    if saved_in or saved_out:
        tok_in = saved_in / CHARS_PER_TOKEN
        tok_out = saved_out / CHARS_PER_TOKEN
        print(
            f"\nAgregado: ~{tok_in / 1000:,.0f}k tokens de entrada y ~{tok_out / 1000:,.0f}k de salida "
            f"que no pasaron por el modelo caro."
        )
        # Opus list price. Only a scale for comparison; the real bill depends on caching.
        cost = tok_in / 1e6 * 5 + tok_out / 1e6 * 25
        print(
            f"A precio de Opus (5 $/M entrada, 25 $/M salida) eso equivale a {cost:.2f} $ "
            f"({tok_in / 1e6 * 5:.2f} $ de entrada + {tok_out / 1e6 * 25:.2f} $ de salida)."
        )

    # Legacy rows predate content_chars, so say so rather than quietly ignoring them.
    old = [r for r in rows if r.get("tool") == "bulk_read" and not r.get("content_chars")]
    if old:
        print(f"\n({len(old)} llamadas antiguas sin content_chars, excluidas del calculo.)")
    if stale_edits:
        print(f"({stale_edits} edicion(es) medidas con el baseline antiguo, excluidas.)")

    refused = [r for r in rows if r.get("event") == "refused-too-small"]
    if refused:
        print(f"\n=== Delegaciones rechazadas por no llegar al umbral: {len(refused)} ===")
        for r in refused[-6:]:
            floor = r.get("break_even_chars")
            # Rows written before the floor was computed from prices have no threshold.
            against = f" contra {floor / 1024:.0f} KB" if floor else ""
            cost = f"  habria costado ${abs(r['net_usd']):.3f}" if r.get("net_usd") else ""
            label = r.get("question") or r.get("target", "")
            print(f"  {r.get('bytes', 0) / 1024:>5.1f} KB{against}{cost}  {label[:40]}")

    reverted = [r for r in rows if r.get("event") == "reverted-syntax-error"]
    rejected = [
        r for r in rows
        if r.get("event") in ("refused-abbreviated", "refused-truncated", "refused-rewrote", "no-change")
    ]
    if reverted or rejected:
        print("\n=== Ediciones delegadas que las guardas detuvieron ===")
        for r in reverted:
            print(f"  revertida (no compilaba)  {r.get('target', '')}")
        for r in rejected:
            print(f"  {r['event'].replace('refused-', 'rechazada: '):<26} {r.get('target', '')}")

    if all_edits:
        print("\n=== Ediciones delegadas aplicadas ===")
        for r in all_edits[-6:]:
            print(
                f"  {r.get('target', '')[:44]:<45} +{r.get('lines_added', 0)}/-{r.get('lines_removed', 0)}"
                f" de {r.get('file_lines', 0)} lineas, sintaxis {r.get('syntax', '?')}"
            )

    blocks = [r for r in rows if r.get("verdict") in ("block", "observe-would-block")]
    if blocks:
        print(f"\n=== Lecturas caras interceptadas: {len(blocks)} ===")

    if writes:
        by_syntax: dict[str, int] = {}
        for r in writes:
            by_syntax[r.get("syntax", "?")] = by_syntax.get(r.get("syntax", "?"), 0) + 1
        print(
            f"\n=== Ficheros generados: {len(writes)} ===\n"
            f"sintaxis: " + ", ".join(f"{v} {k}" for k, v in sorted(by_syntax.items()))
        )
        noref = sum(1 for r in writes if not r.get("references"))
        if noref:
            print(f"generados SIN ficheros de referencia (imports inventados probables): {noref}")
        for r in writes[-5:]:
            print(f"  {r.get('lines'):>4} lineas  {r.get('syntax'):<8} {r.get('target','')[:52]}")

    refused_write = [r for r in rows if r.get("event") == "refused-not-allowed"]
    declined = [r for r in rows if r.get("event") == "worker-declined"]
    if refused_write or declined:
        print(
            f"\nescrituras rechazadas por la allowlist: {len(refused_write)}; "
            f"rehusadas por el worker (material insuficiente): {len(declined)}"
        )

    quality = [r for r in reads if "paths_verified" in r]
    if quality:
        bad = sum(r.get("paths_not_found", 0) + r.get("paths_ambiguous", 0) for r in quality)
        fixed = sum(r.get("paths_corrected", 0) for r in quality)
        ok = sum(r.get("paths_verified", 0) for r in quality)
        uncited = sum(1 for r in quality if r.get("files_uncited"))
        print(
            f"\n=== Calidad ===\nrutas: {ok} correctas, {fixed} corregidas, {bad} marcadas como dudosas"
            f"\nrespuestas que ignoraron algun fichero: {uncited} de {len(quality)}"
        )

    slow = [r for r in reads if r.get("duration_ms")]
    if slow:
        print(
            f"latencia bulk_read: mediana {statistics.median([r['duration_ms'] for r in slow]) / 1000:.1f}s, "
            f"peor {max(r['duration_ms'] for r in slow) / 1000:.1f}s"
        )

    return 0

