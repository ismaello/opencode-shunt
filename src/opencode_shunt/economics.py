"""The delegation break-even, in Python.

A deliberate duplicate of runtime/lib/economics.ts. The runtime needs it in
TypeScript because that is what OpenCode loads; `shunt replay` needs it in
Python because it reads SQLite and joins telemetry, and shelling out to node
would make a reporting command depend on a runtime the user may not have
installed separately.

Two copies of a formula rot apart, and this one decides thresholds rather than
printing numbers, so the rot would be silent and expensive. tests/test_replay.py
runs both implementations over the same inputs and fails if they disagree, which
is the only reason this file is allowed to exist.

Keep the TypeScript as the original: it is the one that runs in production, and
its comments explain why each term is there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Economics:
    cache_write_per_million: float = 6.25
    cache_read_per_million: float = 0.5
    assumed_conversation_tokens: int = 25_000
    extra_turns_per_delegation: int = 2
    remaining_turns: int = 3
    expected_compression: float = 0.13
    chars_per_token: float = 2.9
    safety_margin: float = 1.2
    # Zero is the truth for a worker on your own hardware, and the only honest
    # default for a config written before these keys existed.
    worker_in_per_million: float = 0.0
    worker_out_per_million: float = 0.0

    @classmethod
    def from_config(cls, overrides: dict | None) -> "Economics":
        """Build from a shunt.json "economics" block, which uses camelCase keys."""
        mapping = {
            "cacheWritePerMillion": "cache_write_per_million",
            "cacheReadPerMillion": "cache_read_per_million",
            "assumedConversationTokens": "assumed_conversation_tokens",
            "extraTurnsPerDelegation": "extra_turns_per_delegation",
            "remainingTurns": "remaining_turns",
            "expectedCompression": "expected_compression",
            "charsPerToken": "chars_per_token",
            "safetyMargin": "safety_margin",
            "workerInPerMillion": "worker_in_per_million",
            "workerOutPerMillion": "worker_out_per_million",
        }
        known = {
            mapping[key]: value
            for key, value in (overrides or {}).items()
            if key in mapping and isinstance(value, (int, float))
        }
        return replace(cls(), **known)


@dataclass(frozen=True)
class Verdict:
    worthwhile: bool
    """Infinite when the worker is dearer than the context it saves."""
    break_even_chars: float
    """Net dollars saved by delegating this much, negative when it costs more."""
    net: float


def assess(content_chars: float, economics: Economics, conversation_tokens: float | None = None) -> Verdict:
    conversation = (
        conversation_tokens if conversation_tokens is not None else economics.assumed_conversation_tokens
    )

    kept_out = (content_chars / economics.chars_per_token) * (1 - economics.expected_compression)
    rate_per_kept_token = (
        economics.cache_write_per_million + economics.cache_read_per_million * economics.remaining_turns
    )
    saved = kept_out / 1e6 * rate_per_kept_token

    # The worker reads the content and writes the summary, and both are billed.
    worker_rate_per_token = (
        economics.worker_in_per_million
        + economics.worker_out_per_million * economics.expected_compression
    )
    worker_cost = (content_chars / economics.chars_per_token) / 1e6 * worker_rate_per_token
    paid = (
        conversation * economics.extra_turns_per_delegation / 1e6 * economics.cache_read_per_million
        + worker_cost
    )

    net_rate_per_token = (
        1 - economics.expected_compression
    ) * rate_per_kept_token - worker_rate_per_token

    if net_rate_per_token <= 0:
        # A worker dearer per token than the context it spares: no size makes
        # this pay, and reporting a finite threshold would hide that.
        return Verdict(worthwhile=False, break_even_chars=math.inf, net=saved - paid)

    break_even_tokens = (
        conversation * economics.extra_turns_per_delegation * economics.cache_read_per_million
    ) / net_rate_per_token
    # floor(x + 0.5) rather than round(), to match JavaScript's Math.round:
    # Python rounds halves to even, so round(0.5) is 0 and the two would differ.
    break_even_chars = math.floor(break_even_tokens * economics.chars_per_token * economics.safety_margin + 0.5)

    return Verdict(
        worthwhile=content_chars >= break_even_chars,
        break_even_chars=break_even_chars,
        net=saved - paid,
    )
