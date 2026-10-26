"""OpenRouter generation stats on the call's trace: upstream provider, latency
and the real charge of every LLM call.

The SDK's `llm_request` span only knows `gen_ai.provider.name = openrouter.ai`
and token counts. OpenRouter's `GET /api/v1/generation?id=<gen-…>` adds which
upstream provider served the request, its latency and generation time, and
`total_cost` (https://openrouter.ai/docs/api/api-reference/generations/get-generation).
The generation id is `LLMMetrics.request_id`, collected from the LLM plugin's
`metrics_collected` event, which also covers the end-of-call summary's calls
because they share `session.llm`.

Each fetch runs in its own background task as soon as the generation's metrics
arrive, after a short delay (stats lag the response), so nothing waits on it.
By the time the stats come back the `llm_request` span has ended and been
exported, so they go on a new child span, `openrouter_generation`, parented to
that `llm_request` (found through `LLMRequestIndex`) and given its time range.
At call end `drain` waits, bounded, for the fetches still in flight.

Only created when telemetry is on (see `create_generation_stats`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.util.types import AttributeValue

from cost import KnownLLMCost

logger = logging.getLogger("cadence")

GENERATION_URL = "https://openrouter.ai/api/v1/generation"
SPAN_NAME = "openrouter_generation"
# SDK names (livekit-agents 1.5.6, llm/llm.py, telemetry/trace_types.py).
LLM_REQUEST_SPAN = "llm_request"
LLM_METRICS_ATTR = "lk.llm_metrics"

# Seconds to wait before each attempt. The docs don't say when stats become
# available; a 404 right after the response is expected, so retry on 404, 429,
# 5xx and network errors (about 47 s in all). Probes on 2026-10-25 saw 6-12 s,
# test calls up to 20 s, and one real call 404s for a whole 20 s window.
FETCH_DELAYS = (2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 15.0)
FETCH_TIMEOUT_SECONDS = 5.0

# `data` fields copied to the span, as `openrouter.<name>`. Times are in ms and
# costs in USD (credits), as OpenRouter reports them.
_STAT_FIELDS: dict[str, str] = {
    "id": "openrouter.generation_id",
    "provider_name": "openrouter.provider_name",
    "model": "openrouter.model",
    "latency": "openrouter.latency_ms",
    "generation_time": "openrouter.generation_time_ms",
    "moderation_latency": "openrouter.moderation_latency_ms",
    "total_cost": "openrouter.total_cost_usd",
    "upstream_inference_cost": "openrouter.upstream_inference_cost_usd",
    "cache_discount": "openrouter.cache_discount_usd",
    "tokens_prompt": "openrouter.tokens_prompt",
    "tokens_completion": "openrouter.tokens_completion",
    "native_tokens_prompt": "openrouter.native_tokens_prompt",
    "native_tokens_completion": "openrouter.native_tokens_completion",
    "native_tokens_cached": "openrouter.native_tokens_cached",
    "native_tokens_reasoning": "openrouter.native_tokens_reasoning",
    "finish_reason": "openrouter.finish_reason",
    "streamed": "openrouter.streamed",
    "cancelled": "openrouter.cancelled",
}
# Shown as the observation's output in Langfuse.
_OUTPUT_FIELDS = (
    "provider_name",
    "latency",
    "generation_time",
    "total_cost",
    "native_tokens_prompt",
    "native_tokens_cached",
    "native_tokens_completion",
    "finish_reason",
)


def _http_error(res: httpx.Response) -> str:
    """`HTTP <status>`, plus OpenRouter's `error.message` when the body has one."""
    try:
        message = res.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = None
    if isinstance(message, str) and message:
        return f"HTTP {res.status_code}: {message[:200]}"
    return f"HTTP {res.status_code}"


def generation_attributes(data: dict[str, Any]) -> dict[str, AttributeValue]:
    """Map a `/generation` response's `data` to span attributes.

    Skips missing and null fields. `provider_responses` (one entry per
    upstream attempt, fallbacks included) goes in as a JSON string, plus the
    attempt count, since OTel attributes can't nest.
    """
    attrs: dict[str, AttributeValue] = {}
    for field_name, attr in _STAT_FIELDS.items():
        value = data.get(field_name)
        if isinstance(value, bool | int | float | str):
            attrs[attr] = value
    responses = data.get("provider_responses")
    if isinstance(responses, list):
        attrs["openrouter.provider_attempts"] = len(responses)
        attrs["openrouter.provider_responses"] = json.dumps(responses)
    output = {k: data.get(k) for k in _OUTPUT_FIELDS if data.get(k) is not None}
    attrs["langfuse.observation.output"] = json.dumps(output)
    return attrs


class LLMRequestIndex(SpanProcessor):
    """Remembers each ended `llm_request` span by its OpenRouter generation id.

    The SDK puts the id in the span's `lk.llm_metrics` JSON (`request_id`), and
    the metrics event doesn't carry the span, so this is how a later child span
    finds its parent. Cheap: one small JSON parse per LLM call.
    """

    def __init__(self) -> None:
        self._spans: dict[str, tuple[trace.SpanContext, int | None, int | None]] = {}

    def on_start(
        self, span: Span, parent_context: otel_context.Context | None = None
    ) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        if span.name != LLM_REQUEST_SPAN or span.context is None:
            return
        raw = (span.attributes or {}).get(LLM_METRICS_ATTR)
        if not isinstance(raw, str):
            return
        try:
            request_id = json.loads(raw).get("request_id")
        except (ValueError, AttributeError):
            return
        if isinstance(request_id, str) and request_id:
            self._spans[request_id] = (span.context, span.start_time, span.end_time)

    def get(
        self, generation_id: str
    ) -> tuple[trace.SpanContext, int | None, int | None] | None:
        return self._spans.get(generation_id)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


@dataclass
class _Generation:
    id: str
    input_tokens: int
    output_tokens: int
    stats: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    recorded: bool = False


class GenerationStats:
    """Fetches and records OpenRouter stats for every generation of a call."""

    def __init__(
        self,
        *,
        api_key: str,
        tracer: trace.Tracer,
        index: LLMRequestIndex,
        fallback_parent: trace.Span,
        client: httpx.AsyncClient | None = None,
        delays: Sequence[float] = FETCH_DELAYS,
    ) -> None:
        self._tracer = tracer
        self._index = index
        self._fallback_parent = fallback_parent
        self._client = client or httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        self._delays = tuple(delays)
        self._gens: dict[str, _Generation] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def on_metrics(self, metrics: Any) -> None:
        """`metrics_collected` handler for the LLM plugin (sync, so it only
        schedules the fetch)."""
        gen_id = getattr(metrics, "request_id", None)
        if not isinstance(gen_id, str) or not gen_id.startswith("gen-"):
            return
        if gen_id in self._gens:
            return
        gen = _Generation(
            id=gen_id,
            input_tokens=int(getattr(metrics, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(metrics, "completion_tokens", 0) or 0),
        )
        self._gens[gen_id] = gen
        task = asyncio.create_task(self._fetch(gen), name=f"openrouter_stats_{gen_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fetch(self, gen: _Generation) -> None:
        for delay in self._delays:
            await asyncio.sleep(delay)
            gen.attempts += 1
            try:
                res = await self._client.get(GENERATION_URL, params={"id": gen.id})
            except httpx.HTTPError as e:
                gen.error = f"network error: {type(e).__name__}"
                continue
            if res.status_code == 200:
                try:
                    data = res.json().get("data")
                except ValueError:
                    data = None
                if isinstance(data, dict):
                    gen.stats = data
                    gen.error = None
                else:
                    gen.error = "malformed response"
                break
            gen.error = _http_error(res)
            if (
                res.status_code != 404
                and res.status_code != 429
                and res.status_code < 500
            ):
                break  # 401/403/400: retrying won't help
        self._record(gen)

    def _record(self, gen: _Generation) -> None:
        if gen.recorded:
            return
        gen.recorded = True
        found = self._index.get(gen.id)
        if found is not None:
            span_context, start, end = found
            parent = trace.NonRecordingSpan(span_context)
        else:
            parent, start, end = self._fallback_parent, None, None
        attrs: dict[str, AttributeValue] = {
            "openrouter.generation_id": gen.id,
            "openrouter.fetch_attempts": gen.attempts,
            "openrouter.parent_found": found is not None,
        }
        if gen.stats is not None:
            attrs.update(generation_attributes(gen.stats))
        else:
            reason = f"OpenRouter stats unavailable: {gen.error or 'not fetched'}"
            attrs["openrouter.fetch_error"] = gen.error or "not fetched"
            attrs["langfuse.observation.level"] = "WARNING"
            attrs["langfuse.observation.status_message"] = reason
            logger.warning("%s (%s)", reason, gen.id)
        span = self._tracer.start_span(
            SPAN_NAME,
            context=trace.set_span_in_context(parent),
            attributes=attrs,
            start_time=start,
        )
        span.end(end_time=end)

    def known_cost(self) -> KnownLLMCost:
        """The real charge of the generations whose stats have arrived."""
        fetched = [
            g
            for g in self._gens.values()
            if g.stats is not None
            and isinstance(g.stats.get("total_cost"), int | float)
        ]
        return KnownLLMCost(
            cost_usd=sum(float(g.stats["total_cost"]) for g in fetched),  # type: ignore[index]
            input_tokens=sum(g.input_tokens for g in fetched),
            output_tokens=sum(g.output_tokens for g in fetched),
            generations=len(fetched),
        )

    def call_attributes(self) -> dict[str, AttributeValue]:
        """Call-level totals for the `cadence_call` span."""
        known = self.known_cost()
        providers = sorted(
            {
                str(g.stats["provider_name"])
                for g in self._gens.values()
                if g.stats is not None and g.stats.get("provider_name")
            }
        )
        return {
            "cadence.openrouter.generations": len(self._gens),
            "cadence.openrouter.generations_fetched": known.generations,
            "cadence.openrouter.total_cost_usd": known.cost_usd,
            "cadence.openrouter.providers": providers,
        }

    async def drain(self, timeout: float) -> None:
        """Wait up to `timeout` s for in-flight fetches (including ones started
        while waiting, e.g. the summary's calls), then record the rest as not
        fetched."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while pending := [t for t in self._tasks if not t.done()]:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.wait(pending, timeout=remaining)
        leftover = list(self._tasks)
        for task in leftover:
            task.cancel()
        await asyncio.gather(*leftover, return_exceptions=True)
        for gen in self._gens.values():
            if not gen.recorded:
                gen.error = f"not fetched before call end ({gen.error or 'pending'})"
                self._record(gen)

    async def aclose(self) -> None:
        await self._client.aclose()


def create_generation_stats(
    tracer_provider: TracerProvider | None, call_span: trace.Span
) -> GenerationStats | None:
    """Build the collector, or `None` (no fetches at all) when telemetry is off
    or `OPENROUTER_API_KEY` is unset. Registers the span index on the provider."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if tracer_provider is None or not api_key:
        return None
    index = LLMRequestIndex()
    tracer_provider.add_span_processor(index)
    return GenerationStats(
        api_key=api_key,
        tracer=trace.get_tracer("cadence", tracer_provider=tracer_provider),
        index=index,
        fallback_parent=call_span,
    )
