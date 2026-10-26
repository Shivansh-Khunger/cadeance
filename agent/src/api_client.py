"""Async client for the Hono backend that backs Cadence's appointment tools.

One client lives for the lifetime of an `AgentSession`; build it in
`cadence_session` and close it in the `finally`.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from livekit.agents import get_job_context
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode

DEFAULT_TIMEOUT_SECONDS = 8.0
ROOM_HEADER = "X-Cadence-Room"

# Resolves to the global provider that `telemetry.setup_telemetry` installs in
# the job process; a no-op tracer when tracing is off.
_tracer = trace.get_tracer("cadence.api_client")
_ID_SEGMENT = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/|$)", re.I
)


class ApiError(Exception):
    """Raised when the backend returns a non-2xx response.

    `message` is whatever the API put in the `error` JSON field, so it's safe to
    pass through to the LLM via `ToolError`.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class ApiClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        *,
        room_name: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Sent as X-Cadence-Room on every request, for API log correlation
        # even with tracing off. Defaults to the current job's room.
        if room_name is None:
            job_ctx = get_job_context(required=False)
            room_name = job_ctx.room.name if job_ctx is not None else None
        self._room_name = room_name
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, json: Any | None = None
    ) -> dict[str, Any]:
        url_path = path.split("?", 1)[0]
        template = _ID_SEGMENT.sub("/:id", url_path)
        # A client span per request. Inside a tool it nests under the SDK's
        # `function_tool` span, and the injected `traceparent` makes the API's
        # route and DB spans its children (api/src/telemetry.ts).
        with _tracer.start_as_current_span(
            f"{method} {template}",
            kind=SpanKind.CLIENT,
            attributes={
                "http.request.method": method,
                "url.path": url_path,
                "url.template": template,
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            headers: dict[str, str] = {}
            if self._room_name:
                headers[ROOM_HEADER] = self._room_name
            propagate.inject(headers)
            try:
                res = await self._client.request(
                    method, path, json=json, headers=headers
                )
            except httpx.HTTPError as e:
                span.record_exception(e)
                span.set_attribute("error.type", type(e).__name__)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise ApiError(0, f"Network error: {e}") from e

            span.set_attribute("http.response.status_code", res.status_code)
            if 200 <= res.status_code < 300:
                if res.content:
                    return res.json()
                return {}

            # Try to surface the API's `error` field; fall back to status text.
            try:
                payload = res.json()
                message = payload.get("error") or res.reason_phrase or res.text
            except ValueError:
                message = res.text or res.reason_phrase or "Request failed"
            # HTTP client semconv: any 4xx/5xx is an error on the client span.
            span.set_attribute("error.type", str(res.status_code))
            span.set_status(Status(StatusCode.ERROR, str(message)))
            raise ApiError(res.status_code, str(message))

    # --- User -----------------------------------------------------------------

    async def identify_user(
        self, phone: str, name: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"phone": phone}
        if name:
            body["name"] = name
        return await self._request("POST", "/users/identify", json=body)

    async def get_user(self, phone: str) -> dict[str, Any] | None:
        try:
            return await self._request("GET", f"/users?phone={_q(phone)}")
        except ApiError as e:
            if e.status == 404:
                return None
            raise

    # --- Slots ----------------------------------------------------------------

    async def get_slots(self, days: int = 14) -> list[str]:
        data = await self._request("GET", f"/slots?days={days}")
        return list(data.get("slots", []))

    # --- Appointments ---------------------------------------------------------

    async def book_appointment(
        self, phone: str, starts_at: str, notes: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"phone": phone, "startsAt": starts_at}
        if notes:
            body["notes"] = notes
        return await self._request("POST", "/appointments", json=body)

    async def list_appointments(self, phone: str) -> dict[str, Any]:
        return await self._request("GET", f"/appointments?phone={_q(phone)}")

    async def cancel_appointment(
        self, appointment_id: str, phone: str
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            f"/appointments/{appointment_id}?phone={_q(phone)}",
        )

    async def modify_appointment(
        self,
        appointment_id: str,
        phone: str,
        *,
        starts_at: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if starts_at is not None:
            body["startsAt"] = starts_at
        if notes is not None:
            body["notes"] = notes
        return await self._request(
            "PATCH",
            f"/appointments/{appointment_id}?phone={_q(phone)}",
            json=body,
        )

    # --- Conversations --------------------------------------------------------

    async def save_conversation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/conversations", json=payload)


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
