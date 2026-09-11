"""Tests for the readable bill.

This is a reporting command, so being wrong is cheap in itself. It is not cheap
in what people then do with the number: the whole reason to split a bill by
token class is to decide which tool to reach for, and a split that misattributes
output as input points at the wrong one.

The empty case gets a test because it is the common one. Somebody runs `shunt
costs` in a repository they have never used OpenCode in, and a traceback there
reads as "the tool is broken" rather than "there is nothing to report".
"""

from __future__ import annotations

from opencode_shunt.costs import model_name, render, savings, split_spend, worker_spend


def session(**overrides) -> dict:
    base = {
        "id": "ses_1",
        "model": "claude-opus-4-8",
        "agent": "orchestrator",
        "title": "una tarea",
        "cost": 0.5,
        "input": 1_000,
        "output": 10_000,
        "cache_read": 500_000,
        "cache_write": 100_000,
        "created": 1_757_500_000_000,
    }
    return {**base, **overrides}


# --- the split, which is what the document is for ----------------------------


def test_the_split_reconciles_with_what_was_billed():
    """Measured on the real run: list prices came to the cent on the recorded cost.

    If these drift apart the catalogue is stale, which matters beyond this
    report because the same prices set the delegation floor.
    """
    one = session(cost=0.0, input=1_000_000, output=1_000_000, cache_read=0, cache_write=0)
    split = split_spend(one)
    assert round(split["entrada"], 2) == 5.0
    assert round(split["salida"], 2) == 25.0


def test_output_is_never_folded_into_input():
    """Output bills at five times input, so a total of the two means nothing."""
    split = split_spend(session())
    assert set(split) == {"entrada", "salida", "cache write", "cache read"}


def test_an_unpriced_model_is_assumed_expensive():
    """Understating spend is the misleading direction: it makes this look unneeded."""
    unknown = split_spend(session(model="a-model-from-the-future", output=1_000_000))
    assert unknown["salida"] == 25.0


def test_a_cheap_orchestrator_is_priced_cheaply():
    expensive = split_spend(session(model="claude-opus-4-8"))["salida"]
    cheap = split_spend(session(model="gemini-2.5-pro"))["salida"]
    assert cheap < expensive


# --- the worker's own cost, which no invoice contains ------------------------
#
# These exist because the figure was wrong for weeks. Worker calls are direct
# HTTP and create no OpenCode session, so counting the database alone put them
# at 0.8% of the bill when the true figure is about 2%. The error flattered the
# system, which is the direction an omission must never go.


def test_the_worker_bill_comes_from_telemetry_not_the_database():
    cost, calls, _ = worker_spend(
        [{"local_prompt_tokens": 1_000_000, "local_completion_tokens": 1_000_000}],
        "gemini-2.5-flash",
    )
    assert calls == 1
    assert round(cost, 2) == round(0.30 + 2.50, 2)


def test_a_worker_call_is_priced_as_the_model_it_ran_on():
    rows = [{"local_prompt_tokens": 1_000_000}]
    flash, *_ = worker_spend(rows, "gemini-2.5-flash")
    mini, *_ = worker_spend(rows, "gpt-5.2-mini")
    assert flash != mini


def test_rows_that_are_not_worker_calls_are_not_counted():
    assert worker_spend([{"tool": "read", "bytes": 100}, {"event": "output-shunt"}]) == (0.0, 0, 0)


def test_each_call_is_priced_by_the_model_that_served_it():
    """One tariff for every row put reader and writer on the same price. Where
    those differ it is not a rounding error: a cheap reader and an expensive
    writer turned $10.30 of work into a reported $0.60."""
    rows = [
        {"local_prompt_tokens": 1_000_000, "worker_model": "gemini-2.5-flash"},
        {"local_prompt_tokens": 1_000_000, "worker_model": "gpt-5.2"},
    ]
    mixed, calls, _ = worker_spend(rows, "gemini-2.5-flash")
    assert calls == 2
    assert round(mixed, 2) == round(0.30 + 1.75, 2)


def test_a_free_local_worker_is_free():
    """`or` treated a real zero as absent and substituted a default rate, so
    the one configuration whose cost is certain - a model on your own GPU - was
    billed at Gemini Flash prices."""
    rows = [{"local_prompt_tokens": 5_000_000, "worker_model": "qwen3-coder:30b"}]
    cost, calls, unpriced = worker_spend(rows)
    assert (calls, unpriced) == (1, 0)
    assert cost == 0.0


def test_a_model_we_cannot_price_is_declared_not_guessed():
    rows = [{"local_prompt_tokens": 1_000_000, "worker_model": "nobody-has-heard-of-this"}]
    cost, calls, unpriced = worker_spend(rows)
    assert (cost, calls, unpriced) == (0.0, 1, 1)


def test_unpriced_calls_are_admitted_in_the_report():
    document = render(
        [session(model="claude-opus-4-8")],
        [{"local_prompt_tokens": 1_000_000, "worker_model": "nobody-has-heard-of-this"}],
        "scope",
        None,
    )
    assert "no se han podido tarifar" in document


def test_the_summary_shows_the_worker_bill_beside_the_orchestrator_one():
    document = render(
        [session(cost=1.0)],
        [{"local_prompt_tokens": 1_000_000, "local_completion_tokens": 0}],
        "`/tmp/repo`",
        None,
        "gemini-2.5-flash",
    )
    head = document.split("## Por modelo")[0]
    assert "orquestador:" in head and "workers:" in head
    # The total has to include both, or it is the same omission again.
    assert "$1.30" in head


# --- the model name, which arrives in two shapes -----------------------------


def test_the_model_name_survives_either_encoding():
    assert model_name("claude-opus-4-8") == "claude-opus-4-8"
    assert model_name('{"providerID":"anthropic","id":"claude-opus-4-8"}') == "claude-opus-4-8"


def test_unparseable_model_data_does_not_stop_the_report():
    assert model_name("{not json") == "desconocido"
    assert model_name(None) == "desconocido"


# --- savings ------------------------------------------------------------------


def test_savings_keep_input_and_output_apart():
    rows = [
        {"tool": "bulk_read", "content_chars": 10_000, "returned_chars": 1_000},
        {"tool": "delegate_edit", "written_chars": 2_000, "returned_chars": 200, "metric_version": 2},
    ]
    by_kind = {row["label"]: row for row in savings(rows)}
    assert by_kind["lectura delegada"]["is_output"] is False
    assert by_kind["ediciones delegadas"]["is_output"] is True


def test_edits_measured_by_the_old_metric_are_left_out():
    """The edit saving was recomputed once; mixing the two would inflate it."""
    rows = [{"tool": "delegate_edit", "written_chars": 2_000, "returned_chars": 200}]
    assert savings(rows) == []


def test_telemetry_without_the_fields_is_skipped_rather_than_counted_as_zero():
    assert savings([{"tool": "bulk_read"}, {"event": "worker-failed"}]) == []


# --- rendering ----------------------------------------------------------------


def test_an_empty_repository_gets_an_explanation_not_a_traceback():
    document = render([], [], "`/tmp/nuevo`", None)
    assert "No hay ninguna sesión" in document
    assert "directorio donde se lanzaron" in document


def test_the_document_leads_with_the_number_somebody_asked_for():
    document = render([session()], [], "`/tmp/repo`", None)
    assert "Gasto total" in document.split("## Por modelo")[0]


def test_failed_delegations_are_surfaced():
    """A failed delegation returns the work to the expensive model in silence."""
    document = render(
        [session()],
        [{"event": "worker-failed", "error": "ollama 404: model not found"}],
        "`/tmp/repo`",
        None,
    )
    assert "Delegaciones que fallaron" in document
    assert "404" in document


def test_a_title_with_a_pipe_does_not_break_the_table():
    # Two sessions, because the per-session table only appears above one.
    document = render(
        [session(title="arreglar a | b"), session(id="ses_2")], [], "`/tmp/repo`", None
    )
    rows = [line for line in document.splitlines() if line.startswith("| 10/09")]
    assert rows, "the per-session table did not render"
    assert all(row.count("|") == 6 for row in rows), rows
