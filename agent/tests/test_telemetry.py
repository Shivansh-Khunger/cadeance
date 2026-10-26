import base64

import pytest
from livekit.agents import APIConnectionError, APIStatusError
from livekit.agents.llm import LLMError
from livekit.agents.stt import STTError
from livekit.agents.telemetry import tracer as lk_tracer
from livekit.agents.voice.events import CloseEvent, CloseReason, ErrorEvent
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

import telemetry
from telemetry import (
    LANGFUSE_ENV_VARS,
    CallTrace,
    langfuse_otlp_config,
    session_error_attributes,
    setup_telemetry,
    start_call_span,
)


@pytest.fixture
def no_global_side_effects(monkeypatch: pytest.MonkeyPatch):
    """Keep `setup_telemetry` from touching process-wide state other tests use."""
    global_providers: list[object] = []
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", global_providers.append)
    previous = lk_tracer._tracer_provider
    yield global_providers
    lk_tracer.set_provider(previous)


@pytest.mark.parametrize("missing", LANGFUSE_ENV_VARS)
def test_setup_is_noop_without_langfuse_env(
    monkeypatch: pytest.MonkeyPatch, no_global_side_effects, missing: str
) -> None:
    for name in LANGFUSE_ENV_VARS:
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv(missing)
    previous = lk_tracer._tracer_provider

    assert setup_telemetry("room-1", "job-1") is None
    assert lk_tracer._tracer_provider is previous
    assert no_global_side_effects == []


def test_call_span_is_non_recording_when_telemetry_off() -> None:
    span = start_call_span("room-1", "job-1", None)
    assert not span.is_recording()
    span.end()


def test_langfuse_otlp_config() -> None:
    endpoint, headers = langfuse_otlp_config("pk-lf-1", "sk-lf-2", "http://lf:3100/")
    assert endpoint == "http://lf:3100/api/public/otel/v1/traces"
    assert headers["x-langfuse-ingestion-version"] == "4"
    scheme, token = headers["Authorization"].split(" ")
    assert scheme == "Basic"
    assert base64.b64decode(token).decode() == "pk-lf-1:sk-lf-2"


def test_setup_stamps_session_on_every_span_in_one_trace(
    monkeypatch: pytest.MonkeyPatch, no_global_side_effects
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-2")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://lf:3100")
    exporter = InMemorySpanExporter()
    exporter_kwargs: dict[str, object] = {}

    def fake_exporter(**kwargs: object) -> InMemorySpanExporter:
        exporter_kwargs.update(kwargs)
        return exporter

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", fake_exporter)

    provider = setup_telemetry("room-1", "job-1")
    assert provider is not None
    assert no_global_side_effects == [provider]
    assert lk_tracer._tracer_provider is provider
    assert exporter_kwargs["endpoint"] == "http://lf:3100/api/public/otel/v1/traces"

    # The call span is the root; SDK spans started under it share its trace.
    call_span = start_call_span("room-1", "job-1", provider)
    with (
        trace.use_span(call_span, end_on_exit=True),
        lk_tracer.start_as_current_span("agent_session"),
        lk_tracer.start_as_current_span("llm_request"),
    ):
        pass
    provider.force_flush()

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans) == {"cadence_call", "agent_session", "llm_request"}
    assert len({s.context.trace_id for s in spans.values()}) == 1
    assert spans["cadence_call"].parent is None
    assert spans["agent_session"].parent.span_id == call_span.get_span_context().span_id
    for span in spans.values():
        assert span.attributes["langfuse.session.id"] == "room-1"
        assert span.attributes["lk.room_name"] == "room-1"
        assert span.resource.attributes["service.name"] == "cadence-agent"
        assert span.resource.attributes["service.instance.id"] == "job-1"
    assert spans["cadence_call"].attributes["lk.job_id"] == "job-1"
    provider.shutdown()


# --- Errors -------------------------------------------------------------------


def _llm_error_event(status: int = 404) -> ErrorEvent:
    exc = APIStatusError("No endpoints found", status_code=status)
    return ErrorEvent(
        error=LLMError(timestamp=0, label="openai.LLM", error=exc, recoverable=False),
        source=None,
    )


def test_session_error_attributes_llm_status() -> None:
    attrs = session_error_attributes(_llm_error_event(404))
    assert attrs["cadence.error.source"] == "llm"
    assert attrs["cadence.error.status_code"] == 404
    assert attrs["cadence.error.recoverable"] is False
    assert attrs["cadence.error.type"] == "APIStatusError"
    assert attrs["cadence.error.label"] == "openai.LLM"
    assert "No endpoints found" in attrs["cadence.error.message"]


def test_session_error_attributes_stt_without_status() -> None:
    event = ErrorEvent(
        error=STTError(
            timestamp=0,
            label="deepgram.STT",
            error=APIConnectionError("socket closed"),
            recoverable=True,
        ),
        source=None,
    )
    attrs = session_error_attributes(event)
    assert attrs["cadence.error.source"] == "stt"
    assert attrs["cadence.error.recoverable"] is True
    assert "cadence.error.status_code" not in attrs


@pytest.fixture
def recording():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    call_span = provider.get_tracer("t").start_span("cadence_call")
    yield provider, call_span, exporter
    provider.shutdown()


def test_call_trace_records_error_span_under_current_span(recording) -> None:
    provider, call_span, exporter = recording
    call_trace = CallTrace(provider, call_span)
    tool_span = provider.get_tracer("t").start_span("function_tool")
    with trace.use_span(tool_span, end_on_exit=True):
        call_trace.mark_tool_api_error(503, "db down")
    call_trace.on_session_error(_llm_error_event(429))  # no current span
    call_trace.on_session_close(
        CloseEvent(reason=CloseReason.ERROR, error=_llm_error_event().error)
    )
    call_trace.finish()
    call_span.end()

    spans = exporter.get_finished_spans()
    errors = [s for s in spans if s.name == "cadence_error"]
    assert len(errors) == 2
    tool_error, llm_error = errors
    assert tool_error.parent.span_id == tool_span.get_span_context().span_id
    assert tool_error.status.status_code == StatusCode.ERROR
    assert tool_error.attributes["cadence.error.source"] == "tool"
    assert tool_error.attributes["cadence.error.status_code"] == 503
    assert tool_error.attributes["langfuse.observation.level"] == "ERROR"
    assert llm_error.parent.span_id == call_span.get_span_context().span_id
    assert llm_error.attributes["cadence.error.status_code"] == 429

    tool = next(s for s in spans if s.name == "function_tool")
    assert tool.attributes["cadence.api.status_code"] == 503
    assert tool.status.status_code == StatusCode.ERROR

    call = next(s for s in spans if s.name == "cadence_call")
    assert call.attributes["cadence.close.reason"] == "error"
    assert call.attributes["cadence.errors.count"] == 2
    assert call.status.status_code == StatusCode.ERROR


def test_call_trace_is_noop_when_telemetry_off() -> None:
    call_span = start_call_span("room-1", "job-1", None)
    call_trace = CallTrace(None, call_span)
    assert not call_trace.enabled
    call_trace.on_session_error(_llm_error_event())
    call_trace.mark_tool_api_error(500, "x")
    call_trace.finish()
    assert call_trace.error_count == 0
    call_span.end()
