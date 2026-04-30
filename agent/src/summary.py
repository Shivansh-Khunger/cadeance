"""End-of-call summary generator.

Runs three LLM calls (summary, intent, preferences) plus a fresh fetch of the
user's appointments in parallel, then bundles the result for both the frontend
(`cadence.summary` topic) and the backend (`POST /conversations`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from livekit.agents import llm as lk_llm

from api_client import ApiClient

logger = logging.getLogger("cadence")

TOPIC = "cadence.summary"

VALID_INTENTS = {
    "book_appointment",
    "cancel_appointment",
    "modify_appointment",
    "retrieve_appointments",
    "general_inquiry",
    "other",
}


async def generate_summary(
    *,
    chat_ctx: lk_llm.ChatContext,
    summarizer: lk_llm.LLM,
    api: ApiClient,
    user: dict[str, Any] | None,
    started_at: datetime | None = None,
    room_name: str | None = None,
) -> dict[str, Any]:
    """Compose the summary payload. Never raises — failures degrade gracefully."""
    transcript = _structured_transcript(chat_ctx)
    transcript_text = _format_for_llm(transcript)

    summary_task = _summarize_conversation(summarizer, transcript_text)
    intent_task = _extract_intent(summarizer, transcript_text)
    prefs_task = _extract_preferences(summarizer, transcript_text)
    appts_task = _fetch_appointments(api, user)

    conversation, intent, preferences, appointments = await asyncio.gather(
        summary_task,
        intent_task,
        prefs_task,
        appts_task,
        return_exceptions=False,
    )

    return {
        "conversation": conversation,
        "intent": intent,
        "preferences": preferences,
        "appointments": appointments,
        "transcript": transcript,
        "user": user,
        "room_name": room_name,
        "started_at": started_at.isoformat() if started_at else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _structured_transcript(chat_ctx: lk_llm.ChatContext) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in chat_ctx.items:
        if getattr(item, "type", None) != "message":
            continue
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            continue
        text = (getattr(item, "text_content", "") or "").strip()
        if not text:
            continue
        out.append({"role": role, "text": text})
    return out


def _format_for_llm(items: list[dict[str, str]]) -> str:
    lines = []
    for item in items:
        speaker = "Caller" if item["role"] == "user" else "Cadence"
        lines.append(f"{speaker}: {item['text']}")
    return "\n".join(lines)


async def _summarize_conversation(summarizer: lk_llm.LLM, transcript: str) -> str:
    if not transcript:
        return "No conversation took place."
    ctx = lk_llm.ChatContext()
    ctx.add_message(
        role="system",
        content=(
            "You produce a concise summary of a phone call between a healthcare "
            "front-desk assistant (Cadence) and a caller. Output 2 to 3 short "
            "sentences in plain English, focused on what the caller wanted and "
            "the outcome. No headings, no bullets, no markdown."
        ),
    )
    ctx.add_message(role="user", content=transcript)
    try:
        response = await summarizer.chat(chat_ctx=ctx).collect()
        text = (response.text or "").strip()
        return text or "Summary unavailable."
    except Exception:
        logger.exception("summary LLM call failed")
        return "Summary unavailable."


async def _extract_intent(summarizer: lk_llm.LLM, transcript: str) -> str:
    if not transcript:
        return "unknown"
    ctx = lk_llm.ChatContext()
    ctx.add_message(
        role="system",
        content=(
            "Classify the caller's PRIMARY intent. Reply with EXACTLY ONE label "
            "from this list, lowercase, no other text: book_appointment, "
            "cancel_appointment, modify_appointment, retrieve_appointments, "
            "general_inquiry, other."
        ),
    )
    ctx.add_message(role="user", content=transcript)
    try:
        response = await summarizer.chat(chat_ctx=ctx).collect()
        raw = (response.text or "").strip().lower().replace(" ", "_")
        # Tolerate the model adding extra punctuation/newlines.
        for token in raw.replace("\n", " ").split():
            cleaned = token.strip(".,;:!?\"'`")
            if cleaned in VALID_INTENTS:
                return cleaned
        return "other"
    except Exception:
        logger.exception("intent LLM call failed")
        return "unknown"


async def _extract_preferences(
    summarizer: lk_llm.LLM, transcript: str
) -> list[str]:
    if not transcript:
        return []
    ctx = lk_llm.ChatContext()
    ctx.add_message(
        role="system",
        content=(
            "Extract any explicit preferences the caller mentioned during this "
            "phone call (e.g. 'prefers morning slots', 'allergic to penicillin', "
            "'needs wheelchair access'). Return ONLY a JSON array of short "
            "string bullets, no extra text. Return [] if none."
        ),
    )
    ctx.add_message(role="user", content=transcript)
    try:
        response = await summarizer.chat(chat_ctx=ctx).collect()
        raw = (response.text or "").strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        data = json.loads(raw[start : end + 1])
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()][:6]
        return []
    except Exception:
        logger.exception("preferences LLM call failed")
        return []


async def _fetch_appointments(
    api: ApiClient, user: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if not user or not user.get("phone"):
        return []
    try:
        data = await api.list_appointments(user["phone"])
        return list(data.get("appointments", []))
    except Exception:
        logger.exception("appointments fetch failed during summary")
        return []
