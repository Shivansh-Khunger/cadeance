import json
from types import SimpleNamespace

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import openrouter_stats
from openrouter_stats import (
    GENERATION_URL,
    GenerationStats,
    LLMRequestIndex,
    create_generation_stats,
    generation_attributes,
)

# Shape of a real `GET /api/v1/generation` response (2026-10-25), trimmed.
STATS = {
    "id": "gen-1",
    "model": "deepseek/deepseek-v4.1-flash-20261017",
    "provider_name": "Fireworks",
    "latency": 694,
    "generation_time": 937,
    "moderation_latency": None,
    "total_cost": 7.9161e-05,
    "upstream_inference_cost": 0,
    "cache_discount": 0.000524378,
    "native_tokens_prompt": 2730,
    "native_tokens_completion": 36,
    "native_tokens_cached": 2548,
    "native_tokens_reasoning": 0,
    "finish_reason": "stop",
    "streamed": True,
    "cancelled": False,
    "provider_responses": [
        {"provider_name": "Fireworks", "status": 200, "latency": 692},
    ],
}


def test_generation_attributes() -> None:
    attrs = generation_attributes(STATS)
    assert attrs["openrouter.provider_name"] == "Fireworks"
    assert attrs["openrouter.latency_ms"] == 694
    assert attrs["openrouter.generation_time_ms"] == 937
    assert attrs["openrouter.total_cost_usd"] == 7.9161e-05
    assert attrs["openrouter.native_tokens_cached"] == 2548
    assert attrs["openrouter.cancelled"] is False
    assert "openrouter.moderation_latency_ms" not in attrs  # null is skipped
    assert attrs["openrouter.provider_attempts"] == 1
    assert json.loads(attrs["openrouter.provider_responses"])[0]["status"] == 200
    output = json.loads(attrs["langfuse.observation.output"])
    assert output["provider_name"] == "Fireworks"
    assert output["total_cost"] == 7.9161e-05


@pytest.fixture
def otel():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    index = LLMRequestIndex()
    provider.add_span_processor(index)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    yield SimpleNamespace(
        provider=provider, tracer=tracer, index=index, exporter=exporter
    )
    provider.shutdown()


def _finished_llm_request(tracer, gen_id: str) -> trace.Span:
    span = tracer.start_span("llm_request")
    span.set_attribute("lk.llm_metrics", json.dumps({"request_id": gen_id}))
    span.end()
    return span


def _stats_client(responses: dict[str, list[httpx.Response]]) -> httpx.AsyncClient:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(GENERATION_URL)
        gen_id = request.url.params["id"]
        calls.append(gen_id)
        return responses[gen_id].pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.calls = calls  # type: ignore[attr-defined]
    return client


def _metrics(gen_id: str, prompt: int, completion: int) -> SimpleNamespace:
    return SimpleNamespace(
        request_id=gen_id, prompt_tokens=prompt, completion_tokens=completion
    )


async def test_stats_land_on_child_of_llm_request_after_retry(otel) -> None:
    call_span = otel.tracer.start_span("cadence_call")
    llm_request = _finished_llm_request(otel.tracer, "gen-1")
    client = _stats_client(
        {
            "gen-1": [
                httpx.Response(404, json={"error": {"message": "not found"}}),
                httpx.Response(200, json={"data": STATS}),
            ]
        }
    )
    stats = GenerationStats(
        api_key="unused",
        tracer=otel.tracer,
        index=otel.index,
        fallback_parent=call_span,
        client=client,
        delays=(0, 0, 0),
    )

    stats.on_metrics(_metrics("gen-1", 2544, 35))
    stats.on_metrics(_metrics("gen-1", 2544, 35))  # duplicate id: ignored
    stats.on_metrics(_metrics("", 1, 1))  # no generation id: ignored
    await stats.drain(timeout=5)
    await stats.aclose()

    assert client.calls == ["gen-1", "gen-1"]  # type: ignore[attr-defined]
    (child,) = [
        s
        for s in otel.exporter.get_finished_spans()
        if s.name == "openrouter_generation"
    ]
    assert child.parent.span_id == llm_request.get_span_context().span_id
    assert child.context.trace_id == llm_request.get_span_context().trace_id
    assert child.start_time == llm_request.start_time
    assert child.attributes["openrouter.provider_name"] == "Fireworks"
    assert child.attributes["openrouter.fetch_attempts"] == 2
    assert child.attributes["openrouter.parent_found"] is True

    known = stats.known_cost()
    assert known.generations == 1
    assert known.cost_usd == pytest.approx(7.9161e-05)
    assert (known.input_tokens, known.output_tokens) == (2544, 35)
    call_attrs = stats.call_attributes()
    assert call_attrs["cadence.openrouter.generations"] == 1
    assert call_attrs["cadence.openrouter.providers"] == ["Fireworks"]


async def test_failed_fetch_is_a_warning_and_not_counted_as_cost(otel) -> None:
    call_span = otel.tracer.start_span("cadence_call")
    client = _stats_client(
        {"gen-2": [httpx.Response(401, json={"error": {"message": "no auth"}})]}
    )
    stats = GenerationStats(
        api_key="unused",
        tracer=otel.tracer,
        index=otel.index,
        fallback_parent=call_span,
        client=client,
        delays=(0, 0, 0),
    )

    stats.on_metrics(_metrics("gen-2", 100, 10))
    await stats.drain(timeout=5)
    await stats.aclose()

    assert client.calls == ["gen-2"]  # type: ignore[attr-defined] # 401: no retry
    (child,) = [
        s
        for s in otel.exporter.get_finished_spans()
        if s.name == "openrouter_generation"
    ]
    # No llm_request span with that id: falls back to the call span.
    assert child.parent.span_id == call_span.get_span_context().span_id
    assert child.attributes["openrouter.fetch_error"] == "HTTP 401: no auth"
    assert child.attributes["langfuse.observation.level"] == "WARNING"
    assert stats.known_cost().generations == 0


async def test_drain_records_fetches_still_pending(otel) -> None:
    call_span = otel.tracer.start_span("cadence_call")
    stats = GenerationStats(
        api_key="unused",
        tracer=otel.tracer,
        index=otel.index,
        fallback_parent=call_span,
        client=_stats_client({}),
        delays=(60,),
    )
    stats.on_metrics(_metrics("gen-3", 1, 1))
    await stats.drain(timeout=0.05)
    await stats.aclose()

    (child,) = [
        s
        for s in otel.exporter.get_finished_spans()
        if s.name == "openrouter_generation"
    ]
    assert child.attributes["openrouter.fetch_error"].startswith(
        "not fetched before call end"
    )


def test_create_is_noop_without_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert create_generation_stats(None, trace.INVALID_SPAN) is None


def test_create_needs_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    provider = TracerProvider()
    assert create_generation_stats(provider, trace.INVALID_SPAN) is None
    provider.shutdown()


async def test_create_registers_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    added: list[object] = []
    provider = TracerProvider()
    monkeypatch.setattr(provider, "add_span_processor", added.append)
    stats = create_generation_stats(provider, trace.INVALID_SPAN)
    assert isinstance(stats, openrouter_stats.GenerationStats)
    assert len(added) == 1 and isinstance(added[0], LLMRequestIndex)
    await stats.aclose()
    provider.shutdown()


async def test_404_after_every_attempt_keeps_openrouters_message(otel) -> None:
    call_span = otel.tracer.start_span("cadence_call")
    not_found = {"error": {"message": "Generation gen-4 not found", "code": 404}}
    client = _stats_client(
        {"gen-4": [httpx.Response(404, json=not_found) for _ in range(3)]}
    )
    stats = GenerationStats(
        api_key="unused",
        tracer=otel.tracer,
        index=otel.index,
        fallback_parent=call_span,
        client=client,
        delays=(0, 0, 0),
    )
    stats.on_metrics(_metrics("gen-4", 1, 1))
    await stats.drain(timeout=5)
    await stats.aclose()

    (child,) = [
        s
        for s in otel.exporter.get_finished_spans()
        if s.name == "openrouter_generation"
    ]
    assert child.attributes["openrouter.fetch_attempts"] == 3
    assert (
        child.attributes["openrouter.fetch_error"]
        == "HTTP 404: Generation gen-4 not found"
    )


def test_default_schedule_outlasts_observed_stats_lag() -> None:
    # Stats took 6-12 s to appear in isolated probes and up to 20 s in test
    # calls (2026-10-25), and a real call saw 404s for the whole 20 s window.
    assert sum(openrouter_stats.FETCH_DELAYS) >= 45
