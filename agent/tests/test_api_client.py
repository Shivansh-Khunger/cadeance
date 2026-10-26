import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode

import api_client
from api_client import ROOM_HEADER, ApiClient, ApiError

APPT_ID = "0b6f7f0e-3c1a-4b8e-9a51-2f4d6c8e1a23"


def _client(handler, room_name: str | None = "room-1") -> ApiClient:
    return ApiClient(
        "http://api.test",
        room_name=room_name,
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Route ApiClient spans to a local provider; no global OTel state touched."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(api_client, "_tracer", provider.get_tracer("test"))
    return exporter


async def test_room_header_and_no_traceparent_when_tracing_off() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"slots": ["2026-10-26T09:00:00.000Z"]})

    client = _client(handler)
    assert await client.get_slots(3) == ["2026-10-26T09:00:00.000Z"]
    await client.aclose()

    assert seen[0].headers[ROOM_HEADER] == "room-1"
    assert "traceparent" not in seen[0].headers


async def test_no_room_header_outside_a_job() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={})

    client = _client(handler, room_name=None)
    await client.get_slots()
    await client.aclose()
    assert ROOM_HEADER not in seen[0].headers


async def test_client_span_nests_under_tool_span_and_propagates(
    spans: InMemorySpanExporter,
) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"appointment": {"id": APPT_ID}})

    client = _client(handler)
    tool_tracer = api_client._tracer  # same provider as the fixture
    with tool_tracer.start_as_current_span("function_tool") as tool_span:
        await client.modify_appointment(APPT_ID, "+15550101234", notes="hi")
    await client.aclose()

    (span,) = [s for s in spans.get_finished_spans() if s.name != "function_tool"]
    assert span.name == "PATCH /appointments/:id"
    assert span.kind == SpanKind.CLIENT
    assert span.parent is not None
    assert span.parent.span_id == tool_span.get_span_context().span_id
    assert span.attributes == {
        "http.request.method": "PATCH",
        "url.path": f"/appointments/{APPT_ID}",
        "url.template": "/appointments/:id",
        "http.response.status_code": 200,
    }
    ctx = span.get_span_context()
    assert seen[0].headers["traceparent"] == (
        f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-01"
    )
    assert seen[0].headers[ROOM_HEADER] == "room-1"


async def test_error_response_marks_span_and_still_raises(
    spans: InMemorySpanExporter,
) -> None:
    client = _client(lambda req: httpx.Response(404, json={"error": "User not found"}))
    assert await client.get_user("+15550101234") is None  # 404 -> None, unchanged
    with pytest.raises(ApiError) as exc:
        await client.book_appointment("+15550101234", "2026-10-26T09:00:00.000Z")
    await client.aclose()

    assert exc.value.status == 404 and exc.value.message == "User not found"
    get_span, post_span = spans.get_finished_spans()
    assert post_span.name == "POST /appointments"
    for s in (get_span, post_span):
        assert s.attributes["http.response.status_code"] == 404
        assert s.attributes["error.type"] == "404"
        assert s.status.status_code == StatusCode.ERROR
        assert s.events == ()  # an HTTP error status isn't an exception


async def test_network_error_records_exception(spans: InMemorySpanExporter) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    client = _client(handler)
    with pytest.raises(ApiError) as exc:
        await client.get_slots()
    await client.aclose()

    assert exc.value.status == 0
    (span,) = spans.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error.type"] == "ConnectError"
    assert "http.response.status_code" not in span.attributes
    assert [e.name for e in span.events] == ["exception"]
