"""What each provider can do, and how to talk to it.

This table is what makes the role assignment portable. Everything else in the
system already avoids naming vendors: the worker layer knows three API shapes
(Ollama's, OpenAI's, and Vertex's) rather than a list of companies. What was
missing was a place to record which providers exist, which of the three roles
each can sensibly fill, and what a machine needs before one will work.

The roles are not interchangeable, and conflating them is the most common way to
waste money here:

    orchestrator  decides, designs, reviews. Its own reasoning is the expensive
                  part of the bill, so this is where a frontier model earns its
                  price - and the only role where it does.
    reader        summarises code in bulk for `bulk_read`. The job is obedience
                  to a rigid output format and exact line citations, not
                  brilliance. Cheap and fast wins.
    writer        generates and edits code for `delegate_write` and
                  `delegate_edit`. Genuinely harder than summarising: the result
                  has to compile and match the conventions of the repository.

A provider serving two roles at once is allowed and normal. It is also the case
the original design got wrong, because the shunt decided who to exempt from read
blocking by provider name, which silently assumes one vendor per role.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field

from . import paths


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    """Roles this provider is worth using for, best first."""
    roles: tuple[str, ...]
    """npm package OpenCode loads to talk to it, when it needs one."""
    npm: str | None = None
    """Environment variables that must be set, with a note on how to get them."""
    requires: dict[str, str] = field(default_factory=dict)
    """Models worth offering, keyed by role."""
    models: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Shape of the direct HTTP call the worker layer makes. See runtime/lib/worker.ts."""
    kind: str = "openai-compatible"
    base_url: str | None = None
    """Rough input price per million tokens, for the delegation break-even."""
    price_in: float = 0.0
    """Rough output price per million tokens."""
    price_out: float = 0.0
    """Cache pricing, which is what the break-even actually depends on."""
    cache_write: float | None = None
    cache_read: float | None = None
    """
    Per-model overrides, keyed by model name, for providers whose models differ
    by more than rounding. Google is the case that forces this: the figures above
    are Flash's, and Gemini Pro costs four times as much, so an orchestrator on
    Pro priced as Flash would compute a delegation floor less than half of what
    it should be. Keys are any of price_in, price_out, cache_write, cache_read.
    """
    model_prices: dict[str, dict[str, float]] = field(default_factory=dict)
    """Models this provider is priced for but deliberately does not offer.

    Two different questions get answered by model_prices, and separating them
    matters. "What may the wizard suggest?" is `models`. "What can `shunt
    costs` price if it finds it in a session?" is every key in `model_prices` -
    including models somebody chose in OpenCode directly, without us. Naming
    the intentional gap here keeps the check that every other override key is a
    real model, which is otherwise a silent typo: an override that matches
    nothing simply never applies.
    """
    priced_not_offered: tuple[str, ...] = ()
    context: int = 128_000
    """True when the code leaves the machine. Drives the allowedProviders check."""
    remote: bool = True
    notes: str = ""


CATALOGUE: dict[str, Provider] = {
    "anthropic": Provider(
        id="anthropic",
        label="Anthropic (Claude)",
        # Orchestrator only, and not for reasons of taste. The reader and writer
        # roles are direct HTTP calls made by our own tools, which speak three
        # API shapes: Ollama's, OpenAI's and Vertex's. Anthropic's is none of
        # them - messages rather than chat completions - so offering it as a
        # worker would produce a profile that fails on first use. As the
        # orchestrator it goes through OpenCode, which handles the difference.
        roles=("orchestrator",),
        requires={"ANTHROPIC_API_KEY": "console.anthropic.com, or run: opencode auth login"},
        models={"orchestrator": ("claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-6")},
        # Opus. Cache write is 1.25x input, a read is a tenth.
        price_in=5.0,
        price_out=25.0,
        cache_write=6.25,
        cache_read=0.5,
        model_prices={
            "claude-sonnet-4-6": {
                "price_in": 3.0,
                "price_out": 15.0,
                "cache_write": 3.75,
                "cache_read": 0.30,
            }
        },
        context=200_000,
        notes="The measured cost split on Opus is 47% cache writes, 34% output. Both are what this system attacks.",
    ),
    "google-vertex": Provider(
        id="google-vertex",
        label="Google Gemini (Vertex AI)",
        roles=("reader", "writer", "orchestrator"),
        npm="@ai-sdk/google-vertex",
        requires={
            "GOOGLE_CLOUD_PROJECT": "your GCP project id",
            "VERTEX_LOCATION": "'global' works; a region also works",
        },
        models={
            "orchestrator": ("gemini-3.1-pro-preview", "gemini-2.5-pro"),
            # Newer is not cheaper here: 3.8-flash is $0.75/$3.75 against
            # 2.5-flash's $0.30/$2.50, and 3.1-flash-lite undercuts both. The
            # order is by price, because a reader that follows a rigid format
            # is worth more than a clever one.
            "reader": ("gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-3.8-flash"),
            "writer": ("gemini-3.8-flash", "gemini-2.5-flash", "gemini-3.1-pro-preview"),
        },
        kind="vertex",
        # Flash, which is what fills the reader and writer roles here.
        price_in=0.30,
        price_out=2.50,
        cache_write=0.30,
        # Google discounts a cached token by 75%, where Anthropic discounts by
        # 90%. That ratio, not the absolute price, is what sets the delegation
        # floor, so the difference is not cosmetic.
        cache_read=0.075,
        # Fallbacks only: OpenCode's live table wins when it is readable, which
        # is what keeps these from going stale unnoticed.
        model_prices={
            "gemini-3.1-pro-preview": {
                "price_in": 2.0,
                "price_out": 12.0,
                "cache_write": 2.0,
                "cache_read": 0.2,
            },
            "gemini-2.5-pro": {
                "price_in": 1.25,
                "price_out": 10.0,
                "cache_write": 1.25,
                "cache_read": 0.3125,
            },
            "gemini-3.1-flash-lite": {
                "price_in": 0.25,
                "price_out": 1.50,
                "cache_write": 0.25,
                "cache_read": 0.025,
            },
            "gemini-3.8-flash": {
                "price_in": 0.75,
                "price_out": 3.75,
                "cache_write": 0.75,
                "cache_read": 0.075,
            },
        },
        context=1_000_000,
        notes=(
            "Authenticates with Application Default Credentials, so no API key is stored: "
            "run 'gcloud auth application-default login'. Note gemini-2.5-flash-lite cannot be "
            "an OpenCode agent (it rejects the thinking_config OpenCode sends) but works fine "
            "as a direct worker, which is how this system uses it."
        ),
    ),
    "ollama": Provider(
        id="ollama",
        label="Ollama (local GPU)",
        roles=("reader", "writer"),
        npm="@ai-sdk/openai-compatible",
        models={
            "reader": ("qwen3-coder:30b", "qwen2.5-coder:14b"),
            "writer": ("qwen3-coder:30b",),
        },
        kind="ollama",
        base_url="http://127.0.0.1:11434",
        price_in=0.0,
        price_out=0.0,
        context=32_768,
        remote=False,
        notes=(
            "Free per token and the code never leaves the machine, which is the only option "
            "for a repository that may not go to a cloud. Slower than a cloud worker: measured "
            "median 7s against 5s. Context is limited by VRAM, so keep num_ctx inside the card."
        ),
    ),
    "deepseek": Provider(
        id="deepseek",
        label="DeepSeek",
        roles=("writer", "reader", "orchestrator"),
        npm="@ai-sdk/openai-compatible",
        requires={"DEEPSEEK_API_KEY": "platform.deepseek.com"},
        models={
            "orchestrator": ("deepseek-v4-pro", "deepseek-reasoner"),
            "reader": ("deepseek-v4-flash", "deepseek-chat"),
            "writer": ("deepseek-v4-flash", "deepseek-chat"),
        },
        base_url="https://api.deepseek.com/v1",
        price_in=0.27,
        price_out=1.10,
        # Cache hits are billed at roughly a twentieth of a miss, a steeper ratio
        # than Anthropic's, which raises the point at which delegating pays.
        cache_write=0.27,
        cache_read=0.014,
        context=65_536,
        notes="Strong at code for the price, so worth the writer role rather than the reader one.",
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        roles=("orchestrator", "writer", "reader"),
        requires={"OPENAI_API_KEY": "platform.openai.com"},
        models={
            "orchestrator": ("gpt-6-astra", "gpt-5.2", "gpt-5.2-mini"),
            "reader": ("gpt-5.2-mini",),
            "writer": ("gpt-5.2",),
        },
        base_url="https://api.openai.com/v1",
        # gpt-5.2, the default. Checked against OpenCode's own model table
        # rather than remembered: this entry said 1.25/10 for a while, which is
        # 5.1's pricing, and an orchestrator priced 29% under reality computes
        # a delegation floor 29% too low.
        price_in=1.75,
        price_out=14.0,
        # OpenAI does not charge to write to cache, and reads at a tenth.
        cache_write=1.75,
        cache_read=0.175,
        model_prices={
            "gpt-5.2-mini": {
                "price_in": 0.25,
                "price_out": 2.0,
                "cache_write": 0.25,
                "cache_read": 0.025,
            },
            # Astra is the most expensive orchestrator in this catalogue:
            # exactly twice Opus 4.8 on every axis, and five times Gemini Pro
            # on output. Unlike the rest of OpenAI's line it does bill for
            # cache writes, at 1.25x input, which is what Anthropic does.
            "gpt-6-astra": {
                "price_in": 10.0,
                "price_out": 50.0,
                "cache_write": 12.5,
                "cache_read": 1.0,
            },
            "gpt-6-astra-pro": {
                "price_in": 10.0,
                "price_out": 50.0,
                "cache_write": 12.5,
                "cache_read": 1.0,
            },
            # Not offered as an orchestrator above, deliberately. "fast" costs
            # twice what "astra" does for the same work, so choosing it from a
            # list is almost always a misreading of the name.
            "gpt-6-astra-fast": {
                "price_in": 20.0,
                "price_out": 100.0,
                "cache_write": 25.0,
                "cache_read": 2.0,
            },
        },
        priced_not_offered=("gpt-6-astra-fast", "gpt-6-astra-pro"),
        context=400_000,
        notes=(
            "Astra 6 prices double above a 272k-token context. Nothing here warns you "
            "when you cross it, so treat the figures as a floor on a long conversation."
        ),
    ),
    "opencode-zen": Provider(
        id="opencode-zen",
        label="OpenCode Zen",
        roles=("reader", "writer"),
        npm="@ai-sdk/openai-compatible",
        requires={"OPENCODE_API_KEY": "opencode.ai, included with a Zen subscription"},
        models={"reader": ("grok-code",), "writer": ("qwen3-coder",)},
        base_url="https://opencode.ai/zen/v1",
        price_in=0.0,
        price_out=0.0,
        context=256_000,
        notes="Flat-rate rather than per token, which makes it attractive for the reader role.",
    ),
}


@functools.lru_cache(maxsize=1)
def _live_prices() -> dict[tuple[str, str], dict[str, float | None]]:
    """Prices from OpenCode's own model table, keyed by (provider, model).

    The table below this is a fallback, not the source of truth. Vendors ship
    faster than anyone updates a constant, and the failure is silent: the
    delegation floor comes from these numbers, so a model priced two
    generations out of date shifts every decision without a word. This was
    found in exactly that state.
    """
    try:
        raw = json.loads(paths.opencode_models().read_text())
    except Exception:
        return {}

    table: dict[tuple[str, str], dict[str, float | None]] = {}
    for provider_id, provider in (raw or {}).items():
        if not isinstance(provider, dict):
            continue
        for model_id, model in (provider.get("models") or {}).items():
            cost = (model or {}).get("cost")
            if not isinstance(cost, dict) or cost.get("input") is None:
                continue
            table[(provider_id, model_id)] = {
                "price_in": cost.get("input"),
                "price_out": cost.get("output"),
                # Vertex and OpenAI bill no separate cache write; the input
                # price is what a first pass costs, which is what the
                # break-even needs.
                "cache_write": cost.get("cache_write") if cost.get("cache_write") is not None else cost.get("input"),
                "cache_read": cost.get("cache_read"),
            }
    return table


def refresh_prices() -> None:
    """Forget the cached table. For tests, and for a long-lived process."""
    _live_prices.cache_clear()


def prices_for(provider: Provider, model: str) -> dict[str, float | None]:
    """What one model costs, per million tokens, with provider defaults filled in.

    The delegation floor is computed from the orchestrator's cache prices, so
    getting this wrong does not produce a wrong number on a report: it produces
    a threshold that delegates when it should not, or refuses when it should
    not, silently and on every call. Hence the order: whatever OpenCode says
    today beats whatever was true when this file was written.
    """
    if live := _live_prices().get((provider.id, model)):
        return dict(live)

    base = {
        "price_in": provider.price_in,
        "price_out": provider.price_out,
        "cache_write": provider.cache_write,
        "cache_read": provider.cache_read,
    }
    base.update(provider.model_prices.get(model, {}))
    return base


def prices_for_model(name: str) -> dict[str, float | None] | None:
    """Look prices up from a bare model name, as OpenCode records it.

    OpenCode stores "claude-opus-4-8", with no provider prefix, so this matches
    on the model alone. Longest name first, or "gpt-5.2" would claim a session
    that actually ran on "gpt-5.2-mini" and price it at five times over.

    OpenCode's own table is consulted before the catalogue, which is what lets
    `shunt costs` price a session run on a model we have never heard of rather
    than falling back to a default and reporting a confident wrong number.
    """
    if live_match := _disambiguate(name, exact=True):
        return live_match

    known: list[tuple[str, Provider]] = []
    for provider in CATALOGUE.values():
        for models in provider.models.values():
            for model in models:
                known.append((model, provider))
        for model in provider.model_prices:
            known.append((model, provider))

    for model, provider in sorted(known, key=lambda pair: -len(pair[0])):
        if model in name:
            return prices_for(provider, model)

    return _disambiguate(name, exact=False)


def _disambiguate(name: str, exact: bool) -> dict[str, float | None] | None:
    """Pick one price list when a model name appears under many providers.

    OpenCode's table is a directory of everyone who resells a model, not a
    price list for one vendor: claude-opus-4-8 appears under 21 providers, and
    one of them quotes it at zero. Taking whichever came first produced exactly
    that, and a model believed to be free drives the delegation floor to zero -
    it would have delegated everything, on the grounds that nothing costs
    anything.

    So: the vendor we actually know about wins; failing that, the most
    expensive quote wins, because overstating cost delegates too little and
    understating it delegates work that loses money.
    """
    candidates = [
        (provider_id, model, prices)
        for (provider_id, model), prices in _live_prices().items()
        if (model == name if exact else model in name)
    ]
    # A zero price here means a subscription plan or a broken entry, never a
    # free frontier model.
    priced = [c for c in candidates if (c[2].get("price_in") or 0) > 0] or candidates
    if not priced:
        return None

    known = {p.id for p in CATALOGUE.values()}
    best = max(
        priced,
        key=lambda c: (c[0] in known, len(c[1]), c[2].get("price_in") or 0),
    )
    return dict(best[2])


def for_role(role: str) -> list[Provider]:
    """Providers worth offering for a role, best first.

    "Best" differs by role, which is the whole point of separating them. For the
    orchestrator, capability: it is the one place a frontier model earns its
    price. For the other two, cheapest first, because a reader that summarises
    obediently is worth more than a clever one, and picking an expensive worker
    quietly defeats the entire system.
    """
    candidates = [p for p in CATALOGUE.values() if role in p.roles]
    if role == "orchestrator":
        return sorted(candidates, key=lambda p: -p.price_out)
    return sorted(candidates, key=lambda p: (p.price_in, p.price_out))


def opencode_provider_block(provider: Provider, models: list[str]) -> dict | None:
    """The entry OpenCode needs in opencode.json to reach this provider.

    Providers OpenCode knows natively need nothing here; the rest need an npm
    adapter, a base URL, and - for local models - explicit context limits and a
    declaration that they can call tools, which OpenCode cannot discover.
    """
    if not provider.npm:
        return None

    block: dict = {"npm": provider.npm, "name": provider.label, "options": {}}

    if provider.kind == "vertex":
        block["options"] = {
            "project": "${GOOGLE_CLOUD_PROJECT}",
            "location": "${VERTEX_LOCATION}",
        }
        return block

    if provider.base_url:
        # OpenCode speaks the OpenAI surface, which lives under /v1 on Ollama.
        suffix = "/v1" if provider.kind == "ollama" else ""
        block["options"]["baseURL"] = provider.base_url + suffix

    if provider.kind == "ollama":
        block["models"] = {
            model: {
                "name": model,
                # Neither is discoverable from a local endpoint, and getting
                # either wrong makes the model look broken rather than misconfigured.
                "tool_call": True,
                "limit": {"context": provider.context, "output": 8192},
            }
            for model in models
        }
    return block


def worker_profile(provider: Provider, model: str) -> dict:
    """A profile for runtime/shunt.local.json, describing a direct worker call."""
    profile: dict = {
        "kind": provider.kind,
        "model": model,
        "contextTokens": provider.context,
        "costPerMillionInput": provider.price_in,
    }
    if provider.kind == "vertex":
        # Filled from the environment at call time, so nothing machine-specific
        # or billable-to-someone-else ends up in a config file.
        profile["project"] = "${GOOGLE_CLOUD_PROJECT}"
        profile["location"] = "${VERTEX_LOCATION}"
    else:
        profile["baseURL"] = provider.base_url
    if provider.requires and provider.kind != "vertex":
        # The name of the variable, never the secret itself.
        profile["apiKeyEnv"] = next(iter(provider.requires))
    if provider.kind == "ollama":
        # Keeps the weights resident between calls; without it every question
        # pays a reload of several seconds.
        profile["keepAlive"] = "30m"
    return profile
