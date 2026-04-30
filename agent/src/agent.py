import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    inference,
)
from livekit.agents.llm import ToolError
from livekit.plugins import bey, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from api_client import ApiClient, ApiError
from cost import compute_cost
from summary import TOPIC as SUMMARY_TOPIC
from summary import generate_summary
from tool_events import ToolEventReporter

logger = logging.getLogger("cadence")

load_dotenv(".env.local")

# Hold strong references to background tasks so they aren't garbage-collected
# mid-flight (asyncio only keeps weak refs).
_background_tasks: set[asyncio.Task[None]] = set()

AGENT_MODEL = "xai/grok-4-1-fast-non-reasoning"
DEFAULT_BEY_AVATAR_ID = "694c83e2-8895-4a98-bd16-56332ca3f449"
BEY_AVATAR_ID = os.getenv("BEY_AVATAR_ID", DEFAULT_BEY_AVATAR_ID)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:4000")

# Cartesia Sonic-3 SSML directives. We let the LLM emit these inline so the TTS
# sounds expressive, then strip them from the published transcript so the UI
# shows clean text. The pattern accepts both self-closing and open/close forms
# defensively, even though the prompt only asks for self-closing.
_SSML_TAG = re.compile(r"<[^<>]*?>")
_NONVERBAL = re.compile(
    r"\[(?:laughter|laugh|sigh|cough|breath|gasp)\]",
    re.IGNORECASE,
)
_TERMINAL_PUNCTUATION = {".", "!", "?"}


def _strip_voice_directives(text: str) -> str:
    text = _SSML_TAG.sub("", text)
    text = _NONVERBAL.sub("", text)
    return text


def _safe_emit_split(buffer: str) -> int:
    """Return the largest prefix length of `buffer` that can't contain a
    partially-streamed SSML tag or nonverbal cue. Anything after that index
    must be held until more text arrives (or the stream ends)."""
    safe = len(buffer)
    last_lt = buffer.rfind("<")
    last_gt = buffer.rfind(">")
    if last_lt > last_gt:
        safe = min(safe, last_lt)
    last_lb = buffer.rfind("[")
    last_rb = buffer.rfind("]")
    if last_lb > last_rb:
        safe = min(safe, last_lb)
    return safe


async def _ensure_terminal_punctuation(
    text: AsyncIterable[str],
) -> AsyncGenerator[str, None]:
    # If the LLM finishes on an SSML tag (e.g. `<emotion value="excited"/>`)
    # or trails off without `.`/`!`/`?`, Cartesia closes the stream on a
    # non-utterance boundary and the avatar can clip the final phoneme.
    # Append a period so there's always a clean prosodic ending.
    last_nonws = ""
    async for chunk in text:
        for ch in reversed(chunk):
            if not ch.isspace():
                last_nonws = ch
                break
        yield chunk
    if last_nonws and last_nonws not in _TERMINAL_PUNCTUATION:
        yield "."


def _humanize_time(dt: datetime) -> str:
    """Render just the time portion of a datetime, e.g. '9 AM' or '4:30 PM'."""
    h, m = dt.hour, dt.minute
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def _humanize_slot(iso: str) -> str:
    """Render an ISO datetime as a friendly natural-language phrase."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    weekday = dt.strftime("%A")
    month = dt.strftime("%B")
    return f"{weekday}, {month} {dt.day} at {_humanize_time(dt)}"


def _nearest_slots(slots: list[str], target_iso: str, count: int = 3) -> list[str]:
    """Pick the `count` slots closest in time to `target_iso`. Used when a
    requested slot is taken, to suggest alternatives without a second LLM
    round-trip."""
    if not slots:
        return []
    try:
        target = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
    except ValueError:
        return slots[:count]

    def _distance(iso: str) -> float:
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return abs((dt - target).total_seconds())
        except ValueError:
            return float("inf")

    return sorted(slots, key=_distance)[:count]


@dataclass
class CadenceUserData:
    """Per-session state Cadence's tools share."""

    user: dict[str, Any] | None = None
    api: ApiClient | None = None
    room: rtc.Room | None = None
    last_slot_offer: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    room_name: str | None = None
    # Set by `end_conversation` so the shutdown hook doesn't double-save when
    # the call ends gracefully. Stays False if the caller hangs up.
    finalized: bool = False


def _require_user(context: RunContext[CadenceUserData]) -> dict[str, Any]:
    user = context.userdata.user
    if not user:
        raise ToolError(
            "Please ask the caller for their phone number first so I can "
            "look up their record before doing anything else."
        )
    return user


def _require_api(context: RunContext[CadenceUserData]) -> ApiClient:
    api = context.userdata.api
    if api is None:
        raise ToolError("The booking system is unavailable right now.")
    return api


def _summarize_appointment(appt: dict[str, Any]) -> dict[str, Any]:
    """Trim an appointment row to what the LLM actually needs."""
    return {
        "id": appt["id"],
        "starts_at": appt["startsAt"],
        "starts_at_human": _humanize_slot(appt["startsAt"]),
        "status": appt["status"],
        "notes": appt.get("notes"),
    }


def _build_save_payload(
    *,
    summary: dict[str, Any],
    userdata: "CadenceUserData",
    session: AgentSession[Any] | None,
) -> dict[str, Any]:
    """Compose the body sent to `POST /conversations`, including a per-call
    cost approximation derived from session usage."""
    payload: dict[str, Any] = {
        "phone": userdata.user.get("phone") if userdata.user else None,
        "roomName": userdata.room_name,
        "summary": summary.get("conversation"),
        "intent": summary.get("intent"),
        "preferences": summary.get("preferences", []),
        "transcript": summary.get("transcript", []),
        "startedAt": summary.get("started_at"),
    }
    duration_seconds = 0.0
    if userdata.started_at is not None:
        duration_seconds = (
            datetime.now(timezone.utc) - userdata.started_at
        ).total_seconds()
    try:
        usage = getattr(session, "usage", None) if session is not None else None
        cost = compute_cost(usage, duration_seconds=duration_seconds)
        payload.update(cost)
    except Exception:
        logger.exception("cost approximation failed")
    # The API treats omitted fields as optional but rejects explicit JSON null
    # (zod's `.optional()` accepts undefined, not null). Drop None entries so
    # a hard disconnect with no identified caller still saves cleanly.
    return {k: v for k, v in payload.items() if v is not None}


class Cadence(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""Your name is Cadence. You are the warm, helpful voice assistant for a healthcare clinic's front desk. You help callers identify themselves and book, reschedule, look up, or cancel appointments. Your replies are spoken aloud through Cartesia Sonic-3 TTS, so write for the ear: short, conversational, naturally punctuated.

Style:
- One or two short sentences per turn unless the user asks for more.
- Use normal punctuation. Dates as MM/DD/YYYY (e.g. 04/29/2026). Times with a space before AM/PM (e.g. 3 PM, 10:30 P.M.). Use a single "-" for a brief pause.
- No emojis, markdown, asterisks, or stage directions other than the nonverbalisms below.

Use these Sonic-3 SSML directives inline, sparingly, only when they fit the words:
- <emotion value="X"/> sets the emotional color of the next sentence. Pick from: neutral, content, excited, sympathetic, apologetic, curious, confident, calm, sad, surprised, amazed, grateful, enthusiastic.
- <speed ratio="X"/> changes pace, where X is between 0.6 and 1.5. Slow to ~0.85 when reading important details like phone numbers, dates, or appointment times.
- <volume ratio="X"/> changes loudness, where X is between 0.5 and 2.0. Use rarely.
- [laughter] for a brief, genuine laugh — only when something is actually funny.

Tag rules: only use a directive when it matches the words you are about to say (never tag "sad" before good news). Never read the directive aloud. Place tags before the sentence they apply to. Always end your reply with terminal punctuation (`.`, `!`, or `?`) - never end on an SSML tag or it will clip the final word.

Examples:
- <emotion value="content"/> You're booked for 04/29/2026 at 3 PM. <emotion value="excited"/> See you then!
- Of course - <emotion value="sympathetic"/> I'm sorry to hear that.
- Your reference number is <speed ratio="0.85"/> 5 5 5 - 1 2 3 4.

Tools and behaviour:
- ALWAYS call `identify_user` before any other tool. The phone number is the unique identifier.
- Phone confirmation (CRITICAL): BEFORE calling `identify_user`, read the phone number back at slow pace using `<speed ratio="0.85"/>` and ask "is that correct?". Wait for an explicit "yes" / "correct" / "that's right". Only THEN call `identify_user`. If the caller corrects any digits, read the new number back the same way and re-confirm. Never call `identify_user` until the phone is confirmed — a wrong number creates a wrong record.
- Name capture: pass `name` to `identify_user` whenever the caller has stated one. If you only had the phone the first time and the caller gives their name later, call `identify_user` AGAIN with both phone and name so the record gets updated. Don't leave a caller nameless.
- For booking: call `fetch_slots` first, then offer 3 to 5 nearby options spoken naturally (e.g. "Tuesday at 9 AM, 9:30, or 10 AM"). Only call `book_appointment` once the caller picks one.
- For cancel or reschedule: if the caller doesn't say which appointment, call `retrieve_appointments` first and read back what they have, then act.
- After a successful book or reschedule, confirm date and time aloud with `<speed ratio="0.85"/>` on the time itself.
- Ending the call: when the caller signals they're done ("goodbye", "I'm done", "that's all", "thanks bye"), do NOT call `end_conversation` immediately. First ask: "Anything else, or should I end the call?" — wait for an explicit "yes, end it" / "no, that's all" / "go ahead". Only after that affirmative, call `end_conversation`. If they bring up a new request, handle it instead.

Confirmation rule (CRITICAL): before calling `book_appointment`, `modify_appointment`, or `cancel_appointment`, read back the date and time at slow pace using `<speed ratio="0.85"/>` and wait for an explicit affirmative ("yes", "go ahead", "that's right", "correct"). If the caller hesitates, says "wait", or changes their mind, do NOT call the mutating tool — pivot to the new value and re-confirm.

Ambiguous-time rule: if the caller says vague things like "evening", "around 5", "5ish", "next Friday", "tomorrow", do NOT call `fetch_slots` yet. Ask one short clarifying question with concrete options ("Did you mean 5 PM or 6 PM?", "Which Friday — May 1 or May 8?"). Never assume.

Mid-conversation correction: when the caller changes their mind ("actually wait", "no, make it 6"), absorb the new value smoothly. Don't restart, don't apologize repeatedly — just re-confirm the new value at slow pace and proceed.

Multi-intent: if a single utterance contains both a cancel and a book ("cancel my 5 PM and book 6 PM tomorrow"), execute both. Do the cancel first, then the book. Confirm each step before its tool call.

Off-topic redirect: if the caller asks about weather, news, or anything outside scheduling, answer in one short sentence and pivot back: "I'm just the front-desk scheduler — happy to help with appointments. Was there one I can help with?"

Misheard input: numerals heard as words ("tree pm" → "3 PM", "for thirty" → "4:30") happen often. Always read times back at slow pace before booking. Trust the slow read-back as the safety net rather than guessing.

What to extract from the conversation: name, phone number, date, time, intent. Never invent any of these — ask if missing. Repeat the phone number back at slow pace before saving.

If the user asks who you are, introduce yourself as Cadence at a healthcare front desk.""",
        )

    async def on_enter(self) -> None:
        await self._inject_current_time()
        # Use `say` rather than `generate_reply` so the call always opens with
        # the same warm greeting — no LLM round-trip, no chance of silence on
        # connect, and a consistent first impression.
        await self.session.say(
            "Hi, I'm Cadence at the front desk. How can I help you today?",
        )

    async def _inject_current_time(self) -> None:
        """Anchor the LLM in real time so phrases like 'tomorrow', 'this
        evening', and 'next Friday' map to actual dates. Without this the
        model will guess from training data."""
        now = datetime.now().astimezone()
        weekday = now.strftime("%A")
        date = now.strftime("%B %d, %Y")
        clock = now.strftime("%-I:%M %p") if hasattr(now, "strftime") else ""
        # %-I is GNU-only; fall back to a portable format if it errors.
        try:
            clock = now.strftime("%-I:%M %p")
        except ValueError:
            clock = now.strftime("%I:%M %p").lstrip("0")
        tz = now.tzname() or "local time"
        note = (
            "Current date/time anchor (do not read aloud unless asked): "
            f"today is {weekday}, {date}. Local time is about {clock} {tz}. "
            "Resolve relative phrases like 'tomorrow', 'this evening', "
            "'next Friday' against this anchor when picking slots."
        )
        try:
            chat_ctx = self.chat_ctx.copy()
            chat_ctx.add_message(role="system", content=note)
            await self.update_chat_ctx(chat_ctx)
        except Exception:
            logger.exception("failed to inject current-time anchor")

    async def transcription_node(
        self,
        text: AsyncIterable[str],
        model_settings: object,
    ) -> AsyncGenerator[str, None]:
        # The LLM streams tokens, so an SSML tag like `<emotion value="x"/>`
        # routinely arrives split across multiple chunks. Stripping per-chunk
        # leaks the fragments. Buffer until we have a complete tag, then emit.
        buffer = ""
        async for chunk in text:
            if not isinstance(chunk, str):
                if buffer:
                    cleaned = _strip_voice_directives(buffer)
                    buffer = ""
                    if cleaned:
                        yield cleaned
                yield chunk
                continue
            buffer += chunk
            split = _safe_emit_split(buffer)
            if split > 0:
                emit = _strip_voice_directives(buffer[:split])
                buffer = buffer[split:]
                if emit:
                    yield emit
        if buffer:
            cleaned = _strip_voice_directives(buffer)
            if cleaned:
                yield cleaned

    # --- Tools ---------------------------------------------------------------

    @function_tool()
    async def identify_user(
        self,
        context: RunContext[CadenceUserData],
        phone: str,
        name: str | None = None,
    ) -> str:
        """Look up or create a user by phone number. Use this FIRST, before any
        other tool. The phone number is the caller's unique identifier.

        BEFORE calling this tool, you MUST have read the phone number back to
        the caller (slow pace) and gotten an explicit "yes" / "correct".
        A wrong number creates a wrong record. If you have not yet confirmed
        the digits aloud, ask first and call this tool only after the caller
        confirms.

        ALWAYS pass `name` when the caller has stated a name — even if you
        already called this tool earlier without it. Re-calling with both
        phone and name will update the stored record. Don't leave a caller
        nameless if they've introduced themselves.

        Args:
            phone: The caller's phone number, exactly as stated AND confirmed
                aloud by the caller. Include the country code if mentioned.
                Server normalizes the format.
            name: The caller's name if you have it. Pass it on the first call
                if the caller introduced themselves, or call this tool again
                with phone+name once they share it. Omit only when the caller
                has not given a name yet.
        """
        api = _require_api(context)
        room = context.userdata.room
        async with ToolEventReporter(
            room, "identify_user", "Looking up your record…"
        ) as reporter:
            try:
                data = await api.identify_user(phone, name)
            except ApiError as e:
                raise ToolError(e.message) from e
            user = data["user"]
            context.userdata.user = user
            stored_name = user.get("name")
            display_name = stored_name or "the caller"
            if data.get("isNew"):
                reporter.success(
                    f"Created a new record for {display_name}",
                    detail=user.get("phone"),
                )
                if stored_name:
                    return (
                        f"New caller {stored_name}, phone {user['phone']}. "
                        "Confirm the number back slowly, greet them by name, "
                        "and ask how you can help today."
                    )
                return (
                    f"New caller. Phone {user['phone']}. Confirm the number "
                    "back slowly, then ask their name. Once they give it, "
                    "CALL identify_user AGAIN with the same phone and the "
                    "name to save it on the record. Then ask how you can help."
                )
            reporter.success(
                f"Found {display_name}",
                detail=user.get("phone"),
            )
            recent = data.get("recentConversations") or []
            await self._inject_call_history(recent)
            history_hint = (
                f" {len(recent)} past call(s) loaded into your context — "
                "use them to personalize the conversation, but never read them "
                "verbatim."
                if recent
                else ""
            )
            if not stored_name:
                return (
                    f"Existing caller, phone {user['phone']}, but no name on "
                    "file. Greet them and ask their name. Once they give it, "
                    "CALL identify_user AGAIN with the same phone and the "
                    "name to save it. Then ask how you can help."
                    f"{history_hint}"
                )
            return (
                f"Existing caller: {stored_name}, phone {user['phone']}."
                f"{history_hint} Greet them by name and ask how you can help today."
            )

    async def _inject_call_history(self, recent: list[dict[str, Any]]) -> None:
        """Push a system note summarizing prior calls into chat context so it
        persists for the rest of the session."""
        if not recent:
            return
        lines = [
            "Caller history (most recent first). Reference naturally if "
            "relevant; never read verbatim.",
        ]
        for c in recent:
            ended_at = c.get("endedAt")
            when = _humanize_slot(ended_at) if ended_at else "previously"
            intent = c.get("intent") or "general_inquiry"
            summary = (c.get("summary") or "").strip() or "(no summary)"
            line = f"- {when} — intent: {intent}. {summary}"
            prefs = c.get("preferences") or []
            if prefs:
                line += f" Preferences: {'; '.join(prefs)}"
            lines.append(line)
        try:
            chat_ctx = self.chat_ctx.copy()
            chat_ctx.add_message(role="system", content="\n".join(lines))
            await self.update_chat_ctx(chat_ctx)
        except Exception:
            logger.exception("failed to inject caller history into chat ctx")

    @function_tool()
    async def fetch_slots(
        self,
        context: RunContext[CadenceUserData],
        days_ahead: int = 14,
    ) -> str:
        """Return upcoming open appointment slots, GROUPED BY DAY. Call this
        when the caller wants to book or asks about availability.

        The result is structured so you can offer slots from the right day
        without confusion — each entry has a `day` label (like "Friday, May 1")
        and a list of `slots` for that day. When the caller asks for tomorrow,
        Friday, etc., pick from the matching `day` entry — never invent a day
        name that isn't in the result.

        Args:
            days_ahead: How far ahead to look. Defaults to 14 days.
        """
        api = _require_api(context)
        room = context.userdata.room
        async with ToolEventReporter(
            room, "fetch_slots", "Fetching available slots…"
        ) as reporter:
            try:
                all_slots = await api.get_slots(days_ahead)
            except ApiError as e:
                raise ToolError(e.message) from e

            # Group by calendar day so the LLM can answer "tomorrow at 10"
            # even if today still has slots left. Cap to 5 days, 5 slots each.
            by_day: dict[str, dict[str, Any]] = {}
            for iso in all_slots:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
                key = dt.date().isoformat()
                if key not in by_day:
                    by_day[key] = {
                        "day": f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}",
                        "slots": [],
                    }
                if len(by_day[key]["slots"]) < 5:
                    by_day[key]["slots"].append(
                        {"iso": iso, "time": _humanize_time(dt)}
                    )
            days_out = list(by_day.values())[:5]
            flat = [s["iso"] for d in days_out for s in d["slots"]]
            context.userdata.last_slot_offer = flat
            total = sum(len(d["slots"]) for d in days_out)
            reporter.success(f"Found {total} open slots across {len(days_out)} day(s)")
            if total == 0:
                return (
                    "No open slots in that window. Apologize and offer to "
                    "widen the search."
                )
            return (
                "Available slots, grouped by day. Offer 2-3 options to the "
                "caller in natural language and ALWAYS state the day clearly "
                "(e.g. 'Friday at 9, 9:30, or 10 AM'). If the caller asked "
                "for a specific day, pick from that day's entry only — do "
                f"not substitute another day silently. Slots: {json.dumps(days_out)}"
            )

    @function_tool()
    async def book_appointment(
        self,
        context: RunContext[CadenceUserData],
        starts_at: str,
        notes: str | None = None,
    ) -> str:
        """Book an appointment at the given slot for the identified caller.
        Use only after `identify_user` AND `fetch_slots`, and only when the
        caller has chosen a specific time.

        Args:
            starts_at: ISO datetime of the slot, exactly as returned by
                `fetch_slots`. Do not reformat.
            notes: Optional short note to attach (e.g. reason for visit).
        """
        context.disallow_interruptions()
        api = _require_api(context)
        user = _require_user(context)
        room = context.userdata.room
        async with ToolEventReporter(
            room, "book_appointment", "Booking your appointment…"
        ) as reporter:
            try:
                data = await api.book_appointment(user["phone"], starts_at, notes)
            except ApiError as e:
                if e.status == 409:
                    # Another caller (or another tab) grabbed this slot between
                    # fetch_slots and book. Pull fresh availability and hand
                    # the LLM 2-3 nearby alternatives in a single round-trip.
                    try:
                        available = await api.get_slots(14)
                    except ApiError:
                        available = []
                    nearby = _nearest_slots(available, starts_at, count=3)
                    pretty = (
                        ", ".join(_humanize_slot(s) for s in nearby)
                        or "no nearby openings"
                    )
                    reporter.success(
                        f"Slot taken; offered {len(nearby)} alternatives",
                    )
                    raise ToolError(
                        f"That slot was just taken. Nearby openings: {pretty}. "
                        "Apologize briefly, offer these to the caller, and ask "
                        "which they prefer."
                    ) from e
                raise ToolError(e.message) from e
            pretty = _humanize_slot(data["appointment"]["startsAt"])
            reporter.success(
                f"Booked for {pretty}",
                detail=data["appointment"]["id"],
            )
            return (
                f"Booked. Confirm to the caller that they're set for {pretty}, "
                "slowing down on the date and time."
            )

    @function_tool()
    async def retrieve_appointments(
        self,
        context: RunContext[CadenceUserData],
    ) -> str:
        """List all appointments the identified caller has, including cancelled
        ones. Use this when the caller asks what they have booked, or before
        cancel/modify if it's not clear which appointment they mean.
        """
        api = _require_api(context)
        user = _require_user(context)
        room = context.userdata.room
        async with ToolEventReporter(
            room, "retrieve_appointments", "Pulling up your appointments…"
        ) as reporter:
            try:
                data = await api.list_appointments(user["phone"])
            except ApiError as e:
                raise ToolError(e.message) from e
            rows = [_summarize_appointment(a) for a in data.get("appointments", [])]
            active = [r for r in rows if r["status"] == "booked"]
            reporter.success(
                f"Found {len(rows)} appointments ({len(active)} active)",
            )
            if not rows:
                return "No appointments on file. Tell the caller and offer to book one."
            return f"Appointments: {json.dumps(rows)}"

    @function_tool()
    async def cancel_appointment(
        self,
        context: RunContext[CadenceUserData],
        appointment_id: str,
    ) -> str:
        """Cancel a specific appointment by id. Call `retrieve_appointments`
        first if you don't have the id.

        Args:
            appointment_id: UUID of the appointment to cancel, from
                `retrieve_appointments`.
        """
        context.disallow_interruptions()
        api = _require_api(context)
        user = _require_user(context)
        room = context.userdata.room
        async with ToolEventReporter(
            room, "cancel_appointment", "Cancelling your appointment…"
        ) as reporter:
            try:
                data = await api.cancel_appointment(appointment_id, user["phone"])
            except ApiError as e:
                raise ToolError(e.message) from e
            pretty = _humanize_slot(data["appointment"]["startsAt"])
            reporter.success(f"Cancelled {pretty}")
            return f"Cancelled. Confirm to the caller that {pretty} is now cancelled."

    @function_tool()
    async def modify_appointment(
        self,
        context: RunContext[CadenceUserData],
        appointment_id: str,
        starts_at: str,
    ) -> str:
        """Move a booked appointment to a different slot. Call
        `retrieve_appointments` first if you don't have the id, and
        `fetch_slots` for the new time.

        Args:
            appointment_id: UUID of the appointment to move.
            starts_at: ISO datetime of the new slot, from `fetch_slots`.
        """
        context.disallow_interruptions()
        api = _require_api(context)
        user = _require_user(context)
        room = context.userdata.room
        async with ToolEventReporter(
            room, "modify_appointment", "Moving your appointment…"
        ) as reporter:
            try:
                data = await api.modify_appointment(
                    appointment_id, user["phone"], starts_at=starts_at
                )
            except ApiError as e:
                raise ToolError(e.message) from e
            pretty = _humanize_slot(data["appointment"]["startsAt"])
            reporter.success(f"Moved to {pretty}")
            return (
                f"Moved. Confirm to the caller that the appointment is now "
                f"{pretty}, slowing down on the date and time."
            )

    @function_tool()
    async def end_conversation(
        self,
        context: RunContext[CadenceUserData],
    ) -> str:
        """Wrap up the call and send a written summary to the caller's screen.

        Do NOT call this just because the caller said "bye" or "thanks". You
        must FIRST ask "Anything else, or should I end the call?" and get an
        explicit affirmative ("yes, end it" / "go ahead" / "no, that's all").
        Only after that confirmation, call this tool. If they bring up a new
        request instead, handle it and don't call this tool yet.
        """
        context.disallow_interruptions()
        api = _require_api(context)
        room = context.userdata.room
        user = context.userdata.user
        async with ToolEventReporter(
            room, "end_conversation", "Wrapping up…"
        ) as reporter:
            summary = await generate_summary(
                chat_ctx=self.chat_ctx,
                summarizer=self.session.llm,
                api=api,
                user=user,
                started_at=context.userdata.started_at,
                room_name=context.userdata.room_name,
            )
            if room is not None:
                try:
                    await room.local_participant.send_text(
                        json.dumps(summary), topic=SUMMARY_TOPIC
                    )
                except Exception:
                    logger.exception("failed to publish summary")

            # Persist the conversation. Failures here shouldn't block the
            # goodbye flow — log and move on.
            try:
                await api.save_conversation(
                    _build_save_payload(
                        summary=summary,
                        userdata=context.userdata,
                        session=self.session,
                    )
                )
                # Mark only after a successful save so a failed save here gets
                # a second chance from the shutdown hook.
                context.userdata.finalized = True
            except Exception:
                logger.exception("failed to persist conversation")

            reporter.success("Sent summary to caller")

        # Schedule a graceful close after the goodbye TTS has time to play.
        async def _close_after_goodbye() -> None:
            try:
                await asyncio.sleep(8)
                await self.session.aclose()
            except Exception:
                logger.exception("session close failed")

        task = asyncio.create_task(_close_after_goodbye())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return "Summary sent. Briefly thank the caller, say goodbye, and end the call."


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="cadence")
async def cadence_session(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    userdata = CadenceUserData()
    userdata.api = ApiClient(base_url=API_BASE_URL)
    userdata.room = ctx.room
    userdata.room_name = ctx.room.name
    userdata.started_at = datetime.now(timezone.utc)

    # Set up a voice AI pipeline using OpenAI, Cartesia, Deepgram, and the LiveKit turn detector
    session = AgentSession[CadenceUserData](
        userdata=userdata,
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
        # See all available models at https://docs.livekit.io/agents/models/llm/
        llm=inference.LLM(model=AGENT_MODEL),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=inference.TTS(
            model="cartesia/sonic-3", voice="cbaf8084-f009-4838-a096-07ee2e6612b1"
        ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
        # Setting tts_text_transforms replaces the defaults, so list them
        # explicitly. The terminal-punctuation guard fires after the LLM stream
        # ends and stops Cartesia from cutting the last phoneme when a reply
        # finishes on an SSML tag.
        tts_text_transforms=[
            "filter_emoji",
            "filter_markdown",
            _ensure_terminal_punctuation,
        ],
        # Trip the inactivity nudge after 12s of silence (default is 15s).
        user_away_timeout=12.0,
    )

    avatar = bey.AvatarSession(avatar_id=BEY_AVATAR_ID)
    await avatar.start(session, room=ctx.room)

    agent = Cadence()

    # When the caller goes silent past `user_away_timeout`, the SDK transitions
    # the user state to "away". Nudge them once so the call doesn't stall.
    @session.on("user_state_changed")
    def _on_user_state_changed(ev: object) -> None:
        new_state = getattr(ev, "new_state", None)
        if new_state != "away":
            return
        task = asyncio.create_task(
            session.generate_reply(
                instructions=(
                    "The caller has gone quiet. Gently check in: ask if "
                    "they're still there, in one short sentence."
                )
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    async def _finalize_on_shutdown() -> None:
        """Save the conversation if `end_conversation` didn't run (e.g. caller
        hung up or closed the tab), then tear down the HTTP client."""
        api = userdata.api
        if api is None:
            return
        userdata.api = None
        try:
            if not userdata.finalized:
                try:
                    summary = await generate_summary(
                        chat_ctx=agent.chat_ctx,
                        summarizer=session.llm,
                        api=api,
                        user=userdata.user,
                        started_at=userdata.started_at,
                        room_name=userdata.room_name,
                    )
                    # Skip empty rooms where nobody said anything.
                    if summary.get("transcript"):
                        await api.save_conversation(
                            _build_save_payload(
                                summary=summary,
                                userdata=userdata,
                                session=session,
                            )
                        )
                except Exception:
                    logger.exception("failed to persist conversation on shutdown")
        finally:
            try:
                await api.aclose()
            except Exception:
                logger.exception("api client close failed")

    ctx.add_shutdown_callback(_finalize_on_shutdown)

    # Noise cancellation runs in the browser via @livekit/krisp-noise-filter,
    # so we don't apply it server-side here.
    await session.start(
        agent=agent,
        room=ctx.room,
    )

    # Join the room and connect to the user
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
