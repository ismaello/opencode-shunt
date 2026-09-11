"""Tests for the configuration the wizard generates.

These guard the mistakes this code has already made once, which is the only
reason worth writing most tests. Two of them were found by running the wizard
and reading its output rather than by reasoning:

  - It offered Anthropic as a writer. The worker layer speaks three API shapes
    and Anthropic's is not one of them, so the profile would have failed on
    first use with a confusing error.
  - It put the writer's model in the exempt list. Harmless but wrong, and wrong
    in a way that teaches the reader of that file something untrue: the writer is
    a direct HTTP call, never an OpenCode session, so nothing about it is ever
    exempt from anything.

The third case here has never happened and must never: exempting the
orchestrator switches the whole system off and, because the exemption check runs
before telemetry is written, leaves no trace that it did.
"""

from __future__ import annotations

import io
import json

import pytest

from opencode_shunt import configure, providers
from opencode_shunt.configure import Choices, Measured, build
from opencode_shunt.providers import CATALOGUE


def make(orchestrator: str, reader: str, writer: str, allowed=None) -> dict:
    measured = Measured(conversation_tokens=30_000, remaining_turns=4, sessions=10, test_globs=["tests/**"])
    return build(Choices(orchestrator, reader, writer, allowed, measured))


# --- the catalogue has to match what the runtime can actually call -----------


def test_every_worker_role_uses_a_supported_api_shape():
    """The worker layer implements exactly three; anything else cannot be called."""
    supported = {"ollama", "openai-compatible", "vertex"}
    for provider in CATALOGUE.values():
        if {"reader", "writer"} & set(provider.roles):
            assert provider.kind in supported, f"{provider.id} offers a worker role with kind={provider.kind}"


def test_every_worker_has_somewhere_to_send_the_request():
    for provider in CATALOGUE.values():
        if {"reader", "writer"} & set(provider.roles) and provider.kind != "vertex":
            assert provider.base_url, f"{provider.id} can be a worker but has no base URL"


def test_anthropic_is_orchestrator_only():
    # Its API is messages rather than chat completions, so it cannot be a worker
    # however desirable that might be.
    assert CATALOGUE["anthropic"].roles == ("orchestrator",)


def test_every_provider_offers_models_for_the_roles_it_claims():
    for provider in CATALOGUE.values():
        for role in provider.roles:
            assert provider.models.get(role), f"{provider.id} claims {role} with no models"


def test_worker_roles_are_offered_cheapest_first():
    """An expensive worker quietly defeats the point of the whole system."""
    for role in ("reader", "writer"):
        prices = [p.price_in for p in providers.for_role(role)]
        assert prices == sorted(prices)


def test_the_price_on_screen_is_the_price_of_that_model():
    """Gemini Pro was listed at Flash's price, in the writer role, where picking
    it costs several times the number shown."""
    measured = Measured(conversation_tokens=18_000, remaining_turns=2, sessions=10, test_globs=[])
    labels = dict(configure.role_options("writer", measured))

    pro = next(key for key in labels if "pro" in key and key.startswith("google-vertex/"))
    flash = next(key for key in labels if "flash" in key)
    # The point is the separation, not a specific figure: prices move, and a
    # test pinned to one of them fails for the wrong reason.
    assert labels[pro] != labels[flash]
    assert str(providers.prices_for_model(pro.split("/")[1])["price_in"]) in labels[pro]
    assert "$0.3/M" in labels["google-vertex/gemini-2.5-flash"]


def test_a_free_worker_says_free_rather_than_nothing():
    measured = Measured(conversation_tokens=18_000, remaining_turns=2, sessions=10, test_globs=[])
    labels = dict(configure.role_options("reader", measured))
    ollama = [label for key, label in labels.items() if key.startswith("ollama/")]
    assert ollama and all("free" in label for label in ollama)


# --- the exempt list, which is the dangerous one -----------------------------


def test_the_orchestrator_is_never_exempt():
    """The failure that leaves the system inert while looking merely unused."""
    plan = make("deepseek/deepseek-reasoner", "deepseek/deepseek-chat", "deepseek/deepseek-chat")
    exempt = plan["shared"]["bulkExempt"]
    assert "deepseek" not in exempt
    assert "deepseek/deepseek-reasoner" not in exempt


def test_one_vendor_in_both_roles_still_shunts_the_orchestrator():
    """The case that broke the original provider-keyed design."""
    plan = make("deepseek/deepseek-reasoner", "deepseek/deepseek-chat", "deepseek/deepseek-chat")
    # Exemption has to be per model here, since the provider serves both roles.
    assert plan["shared"]["_roles"]["orchestrator"] == "deepseek/deepseek-reasoner"
    assert all(not entry.endswith("reasoner") for entry in plan["shared"]["bulkExempt"])


def test_the_reader_is_exempt_because_the_explorer_runs_on_it():
    plan = make("anthropic/claude-opus-4-8", "google-vertex/gemini-2.5-flash", "google-vertex/gemini-2.5-flash")
    assert "google-vertex/gemini-2.5-flash" in plan["shared"]["bulkExempt"]


def test_local_providers_are_always_exempt():
    """The design collapses if a local worker cannot read."""
    plan = make("anthropic/claude-opus-4-8", "ollama/qwen3-coder:30b", "ollama/qwen3-coder:30b")
    assert "ollama" in plan["shared"]["bulkExempt"]


# --- profiles ----------------------------------------------------------------


def test_one_profile_when_both_roles_share_a_model():
    plan = make("anthropic/claude-opus-4-8", "google-vertex/gemini-2.5-flash", "google-vertex/gemini-2.5-flash")
    assert len(plan["local"]["profiles"]) == 1
    assert plan["shared"]["profile"] == plan["shared"]["writerProfile"]


def test_two_profiles_when_the_roles_differ():
    plan = make("anthropic/claude-opus-4-8", "ollama/qwen3-coder:30b", "deepseek/deepseek-chat")
    assert len(plan["local"]["profiles"]) == 2
    assert plan["shared"]["profile"] != plan["shared"]["writerProfile"]


def test_the_active_profiles_exist():
    """A name that does not resolve makes bulk_read fail and the saving vanish."""
    plan = make("anthropic/claude-opus-4-8", "ollama/qwen3-coder:30b", "deepseek/deepseek-chat")
    for key in ("profile", "writerProfile"):
        assert plan["shared"][key] in plan["local"]["profiles"]


def test_no_secret_is_ever_written_to_a_file(monkeypatch):
    """Profiles name the variable holding a key, never the key."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-do-not-write-this-down")
    plan = make("anthropic/claude-opus-4-8", "deepseek/deepseek-chat", "deepseek/deepseek-chat")
    serialised = json.dumps(plan)
    assert "sk-do-not-write-this-down" not in serialised
    assert "DEEPSEEK_API_KEY" in serialised


def test_nothing_unexpanded_survives_into_the_config(monkeypatch):
    """A literal ${VAR} in a config file is a silent failure at call time."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    monkeypatch.setenv("VERTEX_LOCATION", "global")
    plan = make("anthropic/claude-opus-4-8", "google-vertex/gemini-2.5-flash", "google-vertex/gemini-2.5-flash")
    assert "${" not in json.dumps(plan)


def test_a_missing_variable_is_omitted_rather_than_left_as_a_placeholder(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_LOCATION", raising=False)
    plan = make("anthropic/claude-opus-4-8", "google-vertex/gemini-2.5-flash", "google-vertex/gemini-2.5-flash")
    profile = next(iter(plan["local"]["profiles"].values()))
    assert "${" not in json.dumps(profile)
    # Absent is better than wrong: the runtime falls back to the environment,
    # and doctor reports it as missing.
    assert profile.get("project", "") == "" or "project" not in profile


# --- economics ---------------------------------------------------------------


def test_measured_habits_reach_the_config():
    plan = make("anthropic/claude-opus-4-8", "google-vertex/gemini-2.5-flash", "google-vertex/gemini-2.5-flash")
    economics = plan["shared"]["economics"]
    assert economics["assumedConversationTokens"] == 30_000
    assert economics["remainingTurns"] == 4


def test_the_orchestrator_sets_the_prices_not_the_worker():
    """The break-even depends on what the expensive model charges, not the cheap one."""
    plan = make("anthropic/claude-opus-4-8", "ollama/qwen3-coder:30b", "ollama/qwen3-coder:30b")
    assert plan["shared"]["economics"]["cacheWritePerMillion"] == CATALOGUE["anthropic"].cache_write


def test_a_run_without_a_terminal_takes_defaults_instead_of_crashing(monkeypatch, capsys):
    """`config --dry-run` died on an EOFError traceback in CI and in any piped
    run, which reads as a broken tool rather than one waiting for input."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    options = [("a", "first"), ("b", "second")]

    assert configure.choose("pick", options, default=1) == "b"
    assert configure.confirm("go ahead?", default=True) is True
    assert configure.confirm("go ahead?", default=False) is False
    assert "no terminal" in capsys.readouterr().out


def test_the_orchestrators_own_model_sets_the_prices_not_its_vendors_cheapest():
    """The defect this found: one price per provider, and Google's were Flash's.

    Gemini Pro orchestrating would have been priced as Flash, four times under,
    and every delegation decision after that is made against the wrong floor.
    """
    gemini = make(
        "google-vertex/gemini-2.5-pro",
        "google-vertex/gemini-2.5-flash",
        "google-vertex/gemini-2.5-flash",
    )
    economics = gemini["shared"]["economics"]
    assert economics["cacheWritePerMillion"] == 1.25
    assert economics["cacheWritePerMillion"] != CATALOGUE["google-vertex"].cache_write


def test_a_cheaper_orchestrator_raises_the_bar_for_delegating():
    """Delegation protects an expensive context. A cheap boss has less to protect.

    Asserted on the ratio rather than a threshold in kilobytes, because that is
    what the runtime derives the floor from: uniformly scaling every price
    leaves the floor unchanged, so only the cache write to cache read ratio
    moves it.
    """

    def ratio(plan):
        economics = plan["shared"]["economics"]
        return economics["cacheWritePerMillion"] / economics["cacheReadPerMillion"]

    opus = make("anthropic/claude-opus-4-8", "ollama/qwen3-coder:30b", "ollama/qwen3-coder:30b")
    pro = make("google-vertex/gemini-2.5-pro", "ollama/qwen3-coder:30b", "ollama/qwen3-coder:30b")
    # A lower ratio means a larger file has to be at stake before delegating pays.
    assert ratio(pro) < ratio(opus)


# --- price lookup, which decides thresholds rather than just reports ---------


def test_a_bare_model_name_finds_its_prices():
    """OpenCode records models without a provider prefix, which is all we get."""
    assert providers.prices_for_model("claude-opus-4-8")["price_out"] == 25.0
    assert providers.prices_for_model("gemini-2.5-pro")["price_out"] == 10.0


def test_the_longest_match_wins():
    """"gpt-5.2" is a prefix of "gpt-5.2-mini", and pricing one as the other
    overstates a session's cost by five times."""
    assert providers.prices_for_model("gpt-5.2-mini")["price_in"] == 0.25
    assert providers.prices_for_model("gpt-5.2")["price_in"] == 1.75
    assert providers.prices_for_model("gemini-2.5-flash-lite")["price_in"] == 0.10


def test_an_unknown_model_says_so_rather_than_guessing():
    assert providers.prices_for_model("some-model-we-have-never-heard-of") is None


# --- reconfiguring must not discard what you set by hand ----------------------
#
# `shunt update` refuses to touch shunt.json even with --force, because it is
# yours. `shunt config` overwrote it wholesale. The file was safe from the
# command that sounds dangerous and rewritten by the one that sounds harmless.


def test_reconfiguring_keeps_policy_the_wizard_never_asked_about(tmp_path):
    path = tmp_path / "shunt.json"
    path.write_text(json.dumps({"allowedProviders": ["ollama"], "profile": "old"}))

    merged, kept = configure.merge_over(path, {"profile": "new"})
    assert merged["allowedProviders"] == ["ollama"]
    assert merged["profile"] == "new"
    assert "allowedProviders" in kept


def test_reconfiguring_keeps_a_widened_allowlist(tmp_path):
    """The README tells people to widen editPaths. Handing them a wizard that
    silently narrows it again is worse than not offering the wizard."""
    path = tmp_path / "shunt.json"
    path.write_text(json.dumps({"editPaths": ["**/*.py"]}))

    merged, kept = configure.merge_over(path, {"editPaths": ["**/test_*.py"]})
    assert merged["editPaths"] == ["**/*.py"]
    assert "editPaths" in kept


def test_reconfiguring_does_rewrite_what_the_models_determine(tmp_path):
    """The opposite failure, and the more expensive one: economics follows from
    the orchestrator you just chose, so keeping the old cache prices computes
    the delegation floor for a model you no longer run."""
    path = tmp_path / "shunt.json"
    path.write_text(json.dumps({"economics": {"cacheWritePerMillion": 6.25}}))

    merged, _ = configure.merge_over(path, {"economics": {"cacheWritePerMillion": 2.0}})
    assert merged["economics"]["cacheWritePerMillion"] == 2.0


def test_an_unreadable_file_is_replaced_rather_than_failing(tmp_path):
    path = tmp_path / "shunt.json"
    path.write_text("{ not json")
    merged, kept = configure.merge_over(path, {"profile": "new"})
    assert merged == {"profile": "new"} and kept == []


# --- prices that do not go stale ---------------------------------------------
#
# The catalogue below was found two generations behind: it offered Gemini 2.5
# Pro while 3.1 Pro was current, and priced GPT-5.2 at 5.1's rates. That is not
# a reporting error - the delegation floor is computed from these numbers, so
# every call was judged against a threshold belonging to a different model.
# OpenCode maintains a current table; these guard that we read it.


@pytest.fixture
def live_table(tmp_path, monkeypatch):
    def write(table: dict) -> None:
        path = tmp_path / "models.json"
        path.write_text(json.dumps(table))
        monkeypatch.setattr("opencode_shunt.paths.opencode_models", lambda: path)
        providers.refresh_prices()

    yield write
    providers.refresh_prices()


def test_opencodes_table_beats_the_catalogue(live_table):
    live_table(
        {"anthropic": {"models": {"claude-opus-5": {"cost": {"input": 99.0, "output": 1.0}}}}}
    )
    assert providers.prices_for(CATALOGUE["anthropic"], "claude-opus-5")["price_in"] == 99.0


def test_a_model_the_catalogue_never_heard_of_is_still_priced(live_table):
    live_table(
        {"someone": {"models": {"brand-new-model": {"cost": {"input": 7.0, "output": 21.0}}}}}
    )
    prices = providers.prices_for_model("brand-new-model")
    assert prices and prices["price_out"] == 21.0


def test_a_missing_table_falls_back_rather_than_failing(monkeypatch, tmp_path):
    monkeypatch.setattr("opencode_shunt.paths.opencode_models", lambda: tmp_path / "absent.json")
    providers.refresh_prices()
    try:
        assert providers.prices_for(CATALOGUE["anthropic"], "claude-opus-4-8")["price_in"] == 5.0
    finally:
        providers.refresh_prices()


def test_a_corrupt_table_falls_back_rather_than_failing(live_table, tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{not json")
    providers.refresh_prices()
    assert providers.prices_for(CATALOGUE["anthropic"], "claude-opus-4-8")["price_in"] == 5.0


def test_a_reseller_quoting_zero_does_not_make_a_model_free(live_table):
    """OpenCode's table lists everyone who resells a model - claude-opus-4-8
    appears under 21 providers, one of them at zero. Taking whichever came
    first did exactly that, and a model believed free drives the delegation
    floor to zero: it would have delegated everything."""
    live_table(
        {
            "kenari": {"models": {"claude-opus-5": {"cost": {"input": 0, "output": 0}}}},
            "anthropic": {"models": {"claude-opus-5": {"cost": {"input": 5.0, "output": 25.0}}}},
        }
    )
    assert providers.prices_for_model("claude-opus-5")["price_in"] == 5.0


def test_the_vendor_we_know_about_wins_over_a_reseller(live_table):
    live_table(
        {
            "someresellerx": {"models": {"claude-opus-5": {"cost": {"input": 6.0, "output": 30.0}}}},
            "anthropic": {"models": {"claude-opus-5": {"cost": {"input": 5.0, "output": 25.0}}}},
        }
    )
    assert providers.prices_for_model("claude-opus-5")["price_in"] == 5.0


def test_among_unknown_vendors_the_dearest_quote_wins(live_table):
    """Overstating cost delegates too little; understating it delegates work
    that loses money. Only one of those is recoverable."""
    live_table(
        {
            "cheapo": {"models": {"mystery-model": {"cost": {"input": 1.0, "output": 2.0}}}},
            "pricey": {"models": {"mystery-model": {"cost": {"input": 9.0, "output": 18.0}}}},
        }
    )
    assert providers.prices_for_model("mystery-model")["price_in"] == 9.0


def test_no_separate_cache_write_means_the_input_price(live_table):
    """Vertex and OpenAI bill no cache write. Reading None as zero would make
    delegating look free and drive the floor to nothing."""
    live_table(
        {"google-vertex": {"models": {"gemini-3.1-pro-preview": {"cost": {"input": 2.0, "output": 12.0, "cache_read": 0.2}}}}}
    )
    prices = providers.prices_for(CATALOGUE["google-vertex"], "gemini-3.1-pro-preview")
    assert prices["cache_write"] == 2.0


def test_every_overridden_model_is_one_the_catalogue_knows():
    """A typo in an override key is silent: it simply never applies.

    `priced_not_offered` is the deliberate exception, for a model somebody may
    choose in OpenCode directly even though the wizard will not suggest it.
    Pricing it is still worth doing so `shunt costs` reports the real figure.
    """
    for provider in CATALOGUE.values():
        offered = {model for models in provider.models.values() for model in models}
        known = offered | set(provider.priced_not_offered)
        for model in provider.model_prices:
            assert model in known, f"{provider.id} prices {model}, which it does not offer"


def test_every_orchestrator_model_can_be_priced():
    """An unpriced orchestrator falls back to a default, i.e. to the wrong floor."""
    for provider in providers.for_role("orchestrator"):
        for model in provider.models["orchestrator"]:
            prices = providers.prices_for(provider, model)
            assert prices["cache_write"] and prices["cache_read"], f"{model} has no cache prices"


# --- opencode.json -----------------------------------------------------------


def test_ollama_models_declare_tool_calling_and_limits():
    """Neither is discoverable from a local endpoint, and both look like bugs when wrong."""
    plan = make("anthropic/claude-opus-4-8", "ollama/qwen3-coder:30b", "ollama/qwen3-coder:30b")
    model = plan["providers"]["ollama"]["models"]["qwen3-coder:30b"]
    assert model["tool_call"] is True
    assert model["limit"]["context"] > 0


def test_providers_opencode_knows_natively_are_not_declared():
    plan = make("anthropic/claude-opus-4-8", "google-vertex/gemini-2.5-flash", "google-vertex/gemini-2.5-flash")
    assert "anthropic" not in plan["providers"]


@pytest.mark.parametrize("role", ["orchestrator", "reader", "writer"])
def test_something_is_available_for_every_role(role):
    assert providers.for_role(role), f"no provider can fill the {role} role"
