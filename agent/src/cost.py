"""Per-call cost approximation.

Reads `AgentSession.usage` (populated by the LiveKit Agents SDK as the call
runs) and turns it into a cents-rounded total plus a breakdown that gets
persisted alongside the conversation row.

Pricing constants are public list rates as of April 2026 — within an order of
magnitude is enough for the dashboard. Update PRICING when rates change; the
breakdown stored on each row makes it easy to recompute historical costs.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("cadence")

# Dollars per unit. Centralized here so a price change is one diff.
PRICING = {
    # LiveKit Inference — Grok-4.1-fast (non-reasoning).
    "llm_input_per_mtok": 0.20,
    "llm_output_per_mtok": 0.50,
    # Deepgram Nova-3 streaming.
    "stt_per_minute": 0.0043,
    # Cartesia Sonic-3.
    "tts_per_mchar": 65.0,
    # Beyond Presence avatar (rough estimate).
    "avatar_per_minute": 0.50,
}


def _to_cents(dollars: float) -> int:
    return max(0, round(dollars * 100))


def _llm_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * PRICING["llm_input_per_mtok"]
        + output_tokens / 1_000_000 * PRICING["llm_output_per_mtok"]
    )


def _stt_cost(audio_seconds: float) -> float:
    return audio_seconds / 60 * PRICING["stt_per_minute"]


def _tts_cost(characters: int) -> float:
    return characters / 1_000_000 * PRICING["tts_per_mchar"]


def _avatar_cost(audio_seconds: float) -> float:
    return audio_seconds / 60 * PRICING["avatar_per_minute"]


def compute_cost(
    session_usage: Any,
    *,
    duration_seconds: float,
) -> dict[str, Any]:
    """Aggregate per-model usage into a flat cost breakdown.

    Returns a dict with `costCents` (int) and `costBreakdown` (dict) suitable
    for the `POST /conversations` payload. Always returns a result even if
    usage is empty — the avatar/duration cost is still meaningful.
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

    if llm_in or llm_out:
        cost = _llm_cost(llm_in, llm_out)
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

    # Avatar pricing is by wall-clock minutes, regardless of who's speaking.
    if duration_seconds > 0:
        cost = _avatar_cost(duration_seconds)
        total_dollars += cost
        breakdown["avatar"] = {
            "audioSeconds": round(duration_seconds, 2),
            "costCents": _to_cents(cost),
        }

    return {
        "costCents": _to_cents(total_dollars),
        "costBreakdown": breakdown,
    }
