"""Async client for the Hono backend that backs Cadence's appointment tools.

One client lives for the lifetime of an `AgentSession`; build it in
`cadence_session` and close it in the `finally`.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 8.0


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
    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, json: Any | None = None
    ) -> dict[str, Any]:
        try:
            res = await self._client.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise ApiError(0, f"Network error: {e}") from e

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
