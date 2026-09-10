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

import json

import pytest

from opencode_shunt import providers
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
