"""Publish tool-call lifecycle events on the `cadence.tool` text-stream topic.

The frontend listens via `room.registerTextStreamHandler("cadence.tool", ...)`
and renders one pill per `id` — re-using the same id between `running` and the
final `success`/`error` so the pill updates in place.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from types import TracebackType
from typing import Any, Self

from livekit import rtc

logger = logging.getLogger("cadence")

TOPIC = "cadence.tool"


async def publish_tool_event(room: rtc.Room | None, payload: dict[str, Any]) -> None:
    if room is None:
        return
    try:
        await room.local_participant.send_text(json.dumps(payload), topic=TOPIC)
    except Exception:
        # Telemetry should never break the tool; just log.
        logger.exception("failed to publish tool event")


class ToolEventReporter:
    """Async context manager that emits running/success/error tool events."""

    def __init__(
        self,
        room: rtc.Room | None,
        name: str,
        running_label: str,
    ) -> None:
        self._room = room
        self._name = name
        self._id = str(uuid.uuid4())
        self._running_label = running_label
        self._success_label: str | None = None
        self._success_detail: str | None = None

    def success(self, label: str, *, detail: str | None = None) -> None:
        """Set the label/detail to use when exiting normally.

        Call this from the tool body once the work succeeds. If unset, the
        running label is reused on success.
        """
        self._success_label = label
        self._success_detail = detail

    async def __aenter__(self) -> Self:
        await publish_tool_event(
            self._room,
            {
                "id": self._id,
                "name": self._name,
                "status": "running",
                "label": self._running_label,
                "ts": _now_ms(),
            },
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None:
            await publish_tool_event(
                self._room,
                {
                    "id": self._id,
                    "name": self._name,
                    "status": "error",
                    "label": str(exc) or "Something went wrong",
                    "ts": _now_ms(),
                },
            )
            return None  # let the exception propagate

        await publish_tool_event(
            self._room,
            {
                "id": self._id,
                "name": self._name,
                "status": "success",
                "label": self._success_label or self._running_label,
                "detail": self._success_detail,
                "ts": _now_ms(),
            },
        )
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)
