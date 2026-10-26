"""Per-call cost approximation.

Reads `AgentSession.usage` (populated by the LiveKit Agents SDK as the call
runs) and turns it into a cents-rounded total plus a breakdown that gets
persisted alongside the conversation row.

The LLM line prefers the real charge: OpenRouter's `total_cost` per generation
(`GET /api/v1/generation`, fetched by `openrouter_stats.py` when telemetry is
on). Tokens of generations whose charge isn't known yet are priced from the
rate table below, so the line is exact, an estimate, or a mix of both
(`merge_llm_cost`). STT and TTS always use the rate table.

Pricing constants are public list rates as of October 2026 for the current
setup (DeepSeek V4.1 Flash via OpenRouter, Deepgram Nova-3 English and
Cartesia direct). Update PRICING when rates change; the breakdown stored on
each row makes it easy to recompute historical costs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("cadence")

# Dollars per unit. Centralized here so a price change is one diff.
PRICING = {
    # DeepSeek V4.1 Flash via OpenRouter: BaseTen standard (20260910 snapshot,
    # current experiment) and Fireworks/Together/BaseTen (1017 snapshot) all
    # list $0.30 in / $1.20 out per Mtok. Checked 2026-10-26 against
    # openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints.
    # Cached input reads are cheaper there but are priced as regular input here
    # (upper bound; OpenRouter's `total_cost` has the real number).
    # Before DeepSeek, x-ai/grok-4.3 (prompts <200k): $1.25 / $2.50.
    "llm_input_per_mtok": 0.30,
    "llm_output_per_mtok": 1.20,
    # OpenRouter bills inference at the provider's list price with no markup
    # and charges a 5.5% platform fee when credits are bought (Standard plan,
    # openrouter.ai/pricing, checked 2026-10-25). Applied to both the rate
    # table and OpenRouter's `total_cost`, which is in credits.
    "openrouter_credit_fee": 0.055,
    # Deepgram Nova-3 streaming, monolingual (language="en"), pay-as-you-go:
    # $0.0048/min, checked 2026-10-25 on deepgram.com/pricing. Deepgram marks
    # streaming rates as limited-time promotional (struck-through $0.0077).
    # Multilingual ("multi", used before) is $0.0058 ($0.0092).
    "stt_per_minute": 0.0048,
    # Cartesia Sonic-3, Pro plan overage ($65 per 1M credits, 1 credit = 1
    # character). Checked 2026-10-25, unchanged.
    "tts_per_mchar": 65.0,
}


@dataclass(frozen=True)
class KnownLLMCost:
    """The real charge for the generations whose OpenRouter stats arrived.

    `cost_usd` is the sum of their `total_cost` (credits, before the platform
    fee); the token counts are what those same generations reported in
    `LLMMetrics`, so the rest of the call's tokens can be priced separately.
    """

    cost_usd: float
    input_tokens: int
    output_tokens: int
    generations: int


def _to_cents(dollars: float) -> int:
    return max(0, round(dollars * 100))


def _llm_cost(input_tokens: int, output_tokens: int) -> float:
    list_cost = (
        input_tokens / 1_000_000 * PRICING["llm_input_per_mtok"]
        + output_tokens / 1_000_000 * PRICING["llm_output_per_mtok"]
    )
    return list_cost * (1 + PRICING["openrouter_credit_fee"])


def merge_llm_cost(
    input_tokens: int, output_tokens: int, known: KnownLLMCost | None
) -> tuple[float, str]:
    """Dollar cost of the call's LLM usage, and where the number came from.

    Generations with a known OpenRouter charge count at that charge; the
    tokens not covered by them are priced from the rate table. Returns the
    source as `"openrouter"` (fully covered), `"mixed"` or `"rate_table"`.
    """
    if known is None or known.generations == 0:
        return _llm_cost(input_tokens, output_tokens), "rate_table"
    rest_in = max(0, input_tokens - known.input_tokens)
    rest_out = max(0, output_tokens - known.output_tokens)
    cost = known.cost_usd * (1 + PRICING["openrouter_credit_fee"])
    if rest_in or rest_out:
        return cost + _llm_cost(rest_in, rest_out), "mixed"
    return cost, "openrouter"


def _stt_cost(audio_seconds: float) -> float:
    return audio_seconds / 60 * PRICING["stt_per_minute"]


def _tts_cost(characters: int) -> float:
    return characters / 1_000_000 * PRICING["tts_per_mchar"]


def compute_cost(
    session_usage: Any,
    *,
    duration_seconds: float,
    known_llm: KnownLLMCost | None = None,
) -> dict[str, Any]:
    """Aggregate per-model usage into a flat cost breakdown.

    Returns a dict with `costCents` (int) and `costBreakdown` (dict) suitable
    for the `POST /conversations` payload. Always returns a result even if
    usage is empty — `durationSeconds` is still recorded. `known_llm` is the
    real OpenRouter charge collected so far (see `merge_llm_cost`); the
    breakdown's shape doesn't change with it.
    """
    breakdown: dict[str, Any] = {"durationSeconds": round(duration_seconds, 2)}
    total_dollars = 0.0

    llm_in = 0
    llm_out = 0
    stt_seconds = 0.0
    tts_chars = 0

    model_usage = getattr(session_usage, "model_usage", None) or []
    for usage in model_usage:
        # The SDK splits usage by provider/model. Sum across all of them; we
        # only price by capability, not provider.
        kind = type(usage).__name__
        if kind == "LLMModelUsage":
            llm_in += int(getattr(usage, "input_tokens", 0) or 0)
            llm_out += int(getattr(usage, "output_tokens", 0) or 0)
        elif kind == "STTModelUsage":
            stt_seconds += float(getattr(usage, "audio_duration", 0) or 0)
        elif kind == "TTSModelUsage":
            tts_chars += int(getattr(usage, "characters_count", 0) or 0)
        else:
            logger.debug("unknown usage kind %s", kind)

    if llm_in or llm_out or (known_llm is not None and known_llm.generations):
        cost, _source = merge_llm_cost(llm_in, llm_out, known_llm)
        total_dollars += cost
        breakdown["llm"] = {
            "inputTokens": llm_in,
            "outputTokens": llm_out,
            "costCents": _to_cents(cost),
        }

    if stt_seconds > 0:
        cost = _stt_cost(stt_seconds)
        total_dollars += cost
        breakdown["stt"] = {
            "audioSeconds": round(stt_seconds, 2),
            "costCents": _to_cents(cost),
        }

    if tts_chars > 0:
        cost = _tts_cost(tts_chars)
        total_dollars += cost
        breakdown["tts"] = {
            "characters": tts_chars,
            "costCents": _to_cents(cost),
        }

    return {
        "costCents": _to_cents(total_dollars),
        "costBreakdown": breakdown,
    }
