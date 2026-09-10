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

from dataclasses import dataclass, field


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
        models={"orchestrator": ("claude-opus-4-8", "claude-sonnet-4-8")},
        price_in=5.0,
        price_out=25.0,
        cache_write=6.25,
        cache_read=0.5,
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
            "orchestrator": ("gemini-2.5-pro",),
            "reader": ("gemini-2.5-flash", "gemini-2.5-flash-lite"),
            "writer": ("gemini-2.5-flash", "gemini-2.5-pro"),
        },
        kind="vertex",
        price_in=0.30,
        price_out=2.50,
        cache_write=0.375,
        cache_read=0.03,
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
            "orchestrator": ("deepseek-reasoner",),
            "reader": ("deepseek-chat",),
            "writer": ("deepseek-chat",),
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
            "orchestrator": ("gpt-5.2", "gpt-5.2-mini"),
            "reader": ("gpt-5.2-mini",),
            "writer": ("gpt-5.2",),
        },
        base_url="https://api.openai.com/v1",
        price_in=1.25,
        price_out=10.0,
        cache_write=1.25,
        cache_read=0.125,
        context=400_000,
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
