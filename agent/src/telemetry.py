"""OpenTelemetry traces for a call, exported to Langfuse over OTLP/HTTP.

LiveKit Agents 1.5.6 already creates the spans (`agent_session`, `user_turn`,
`eou_detection`, `agent_turn`, `llm_node`, `llm_request`, `function_tool`,
`tts_node`, ...), but sends them to a no-op provider unless one is set. Each
job runs in its own process, so `setup_telemetry` is called inside the job
(`cadence_session`), not at import time.

Telemetry is off, and every function here is a no-op, unless all three of
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` are set.

`CallTrace` adds what the SDK spans miss: errors (STT/LLM/TTS `error` events,
tool and API failures) as `cadence_error` spans with status ERROR, the close
reason, and call-level totals on the `cadence_call` root span. OpenRouter
generation stats live in `openrouter_stats.py`.

Endpoint and headers follow https://langfuse.com/integrations/native/opentelemetry
and https://docs.livekit.io/testing/observability/tracing/ (Langfuse example).
"""

from __future__ import annotations

import base64
import os
from typing import Any

from livekit.agents import telemetry as lk_telemetry
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_INSTANCE_ID, SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import AttributeValue

LANGFUSE_ENV_VARS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
SERVICE = "cadence-agent"
CALL_SPAN_NAME = "cadence_call"
ERROR_SPAN_NAME = "cadence_error"

# Langfuse v4 stores session and user per observation, not per trace, so the
# session id has to be on every span. The SDK's `metadata` argument does that:
# it adds a span processor that stamps these attributes on every span the
# provider starts, including our own spans created through the global tracer.
SESSION_ID_ATTR = "langfuse.session.id"
ROOM_NAME_ATTR = "lk.room_name"
JOB_ID_ATTR = "lk.job_id"


def langfuse_otlp_config(
    public_key: str, secret_key: str, base_url: str
) -> tuple[str, dict[str, str]]:
    """Return the OTLP/HTTP traces endpoint and headers for a Langfuse project."""
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    endpoint = f"{base_url.rstrip('/')}/api/public/otel/v1/traces"
    headers = {
        "Authorization": f"Basic {auth}",
        # Real-time ingestion; without it spans can take up to 10 min to show.
        "x-langfuse-ingestion-version": "4",
    }
    return endpoint, headers


def setup_telemetry(room_name: str, job_id: str) -> TracerProvider | None:
    """Route this job's spans to Langfuse, grouped into a session per room.

    Returns the provider so the caller can flush it at shutdown, or `None`
    (and changes nothing) when any `LANGFUSE_*` variable is unset.
    """
    public_key, secret_key, base_url = (os.environ.get(k) for k in LANGFUSE_ENV_VARS)
    if not (public_key and secret_key and base_url):
        return None

    endpoint, headers = langfuse_otlp_config(public_key, secret_key, base_url)
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: SERVICE, SERVICE_INSTANCE_ID: job_id})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
    )
    # 1.5.6 signature: set_tracer_provider(tracer_provider, *, metadata=None);
    # there is no `allow_pii` before 1.8.x, so transcripts, tool arguments and
    # phone numbers are exported as-is (spec, open decision on PII).
    lk_telemetry.set_tracer_provider(
        provider,
        metadata={SESSION_ID_ATTR: room_name, ROOM_NAME_ATTR: room_name},
    )
    # Our own spans (ApiClient, the call span below) use the global provider.
    trace.set_tracer_provider(provider)
    return provider


def start_call_span(
    room_name: str, job_id: str, tracer_provider: TracerProvider | None = None
) -> trace.Span:
    """Start the root span of the call's trace.

    The SDK's own root, `job_entrypoint`, starts before `cadence_session` runs,
    so it comes from the no-op provider and is dropped; `agent_session` would
    then be the root, and spans started after the session closes (the summary's
    LLM calls in the shutdown callback) would each start a separate trace.
    Making this span current around `session.start` and the shutdown work keeps
    the whole call in one trace. The caller ends it. Pass the provider from
    `setup_telemetry`; with `None` it uses the global one, which is a no-op
    (non-recording span) when telemetry is off.
    """
    return trace.get_tracer("cadence", tracer_provider=tracer_provider).start_span(
        CALL_SPAN_NAME,
        attributes={ROOM_NAME_ATTR: room_name, JOB_ID_ATTR: job_id},
    )


# --- Errors -------------------------------------------------------------------
#
# Each error becomes a small `cadence_error` span with OTel status ERROR, under
# the span that was current when it happened (e.g. `llm_request` for an LLM
# error, `function_tool` for a tool's API error), else under `cadence_call`.
# Langfuse shows it as an observation with level ERROR, so errors can be
# filtered by level, by name, or by the `cadence.error.*` attributes.

# Langfuse reads these before OTel status (langfuse.com/integrations/native/opentelemetry).
LANGFUSE_LEVEL_ATTR = "langfuse.observation.level"
LANGFUSE_STATUS_MESSAGE_ATTR = "langfuse.observation.status_message"

ERROR_SOURCE_ATTR = "cadence.error.source"
ERROR_MESSAGE_ATTR = "cadence.error.message"
ERROR_TYPE_ATTR = "cadence.error.type"
ERROR_STATUS_CODE_ATTR = "cadence.error.status_code"
ERROR_RECOVERABLE_ATTR = "cadence.error.recoverable"
ERROR_LABEL_ATTR = "cadence.error.label"
API_STATUS_CODE_ATTR = "cadence.api.status_code"

# `type` of the SDK's error models (1.5.6) -> our source label.
_SOURCE_BY_ERROR_TYPE = {
    "llm_error": "llm",
    "realtime_model_error": "llm",
    "stt_error": "stt",
    "tts_error": "tts",
    "interruption_detection_error": "interruption",
}
_MAX_MESSAGE = 2000


def session_error_attributes(event: Any) -> dict[str, AttributeValue]:
    """Attributes for an `AgentSession` `error` event (`ErrorEvent`).

    `event.error` is an `LLMError`/`STTError`/`TTSError`/... model wrapping the
    exception; the status code comes from `APIStatusError.status_code`.
    """
    err = getattr(event, "error", None)
    exc = getattr(err, "error", None)
    if exc is None and isinstance(err, BaseException):
        exc = err
    attrs: dict[str, AttributeValue] = {
        ERROR_SOURCE_ATTR: _SOURCE_BY_ERROR_TYPE.get(
            getattr(err, "type", ""), "unknown"
        ),
        ERROR_MESSAGE_ATTR: str(exc if exc is not None else err)[:_MAX_MESSAGE],
    }
    recoverable = getattr(err, "recoverable", None)
    if isinstance(recoverable, bool):
        attrs[ERROR_RECOVERABLE_ATTR] = recoverable
    label = getattr(err, "label", None)
    if isinstance(label, str) and label:
        attrs[ERROR_LABEL_ATTR] = label
    if exc is not None:
        attrs[ERROR_TYPE_ATTR] = type(exc).__name__
        status = getattr(exc, "status_code", None)
        # APIStatusError defaults status_code to -1 when it's unknown.
        if isinstance(status, int) and status >= 0:
            attrs[ERROR_STATUS_CODE_ATTR] = status
    return attrs


def api_error_attributes(
    source: str, status: int, message: str
) -> dict[str, AttributeValue]:
    """Attributes for a failed call to our API (`ApiError`; status 0 = network)."""
    return {
        ERROR_SOURCE_ATTR: source,
        ERROR_MESSAGE_ATTR: message[:_MAX_MESSAGE],
        ERROR_TYPE_ATTR: "ApiError",
        ERROR_STATUS_CODE_ATTR: status,
        ERROR_RECOVERABLE_ATTR: True,
    }


class CallTrace:
    """Errors and call-level attributes for one call's trace.

    Every method is a no-op when telemetry is off (`tracer_provider` is None).
    """

    def __init__(
        self, tracer_provider: TracerProvider | None, call_span: trace.Span
    ) -> None:
        self._enabled = tracer_provider is not None and call_span.is_recording()
        self._tracer = trace.get_tracer("cadence", tracer_provider=tracer_provider)
        self._call_span = call_span
        self.error_count = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record_error(
        self,
        attributes: dict[str, AttributeValue],
        *,
        exception: BaseException | None = None,
    ) -> None:
        """Record one error as a `cadence_error` span with status ERROR."""
        if not self._enabled:
            return
        parent = trace.get_current_span()
        if not parent.get_span_context().is_valid:
            parent = self._call_span
        message = str(attributes.get(ERROR_MESSAGE_ATTR, "error"))
        source = attributes.get(ERROR_SOURCE_ATTR, "unknown")
        status_message = f"{source}: {message}"
        span = self._tracer.start_span(
            ERROR_SPAN_NAME,
            context=trace.set_span_in_context(parent),
            attributes={
                **attributes,
                LANGFUSE_LEVEL_ATTR: "ERROR",
                LANGFUSE_STATUS_MESSAGE_ATTR: status_message,
            },
        )
        if exception is not None:
            span.record_exception(exception)
        span.set_status(Status(StatusCode.ERROR, status_message))
        span.end()
        self.error_count += 1

    def on_session_error(self, event: Any) -> None:
        """`AgentSession` `error` handler (STT/LLM/TTS errors, with `recoverable`)."""
        exc = getattr(getattr(event, "error", None), "error", None)
        self.record_error(
            session_error_attributes(event),
            exception=exc if isinstance(exc, BaseException) else None,
        )

    def on_session_close(self, event: Any) -> None:
        """`AgentSession` `close` handler: the close reason goes on the call
        span, which is marked ERROR when the session closed on an error."""
        if not self._enabled:
            return
        reason = getattr(event, "reason", None)
        self._call_span.set_attribute(
            "cadence.close.reason", str(getattr(reason, "value", reason))
        )
        err = getattr(event, "error", None)
        if err is not None:
            message = f"session closed on error: {getattr(err, 'error', err)}"
            self._call_span.set_attributes(
                {
                    LANGFUSE_LEVEL_ATTR: "ERROR",
                    LANGFUSE_STATUS_MESSAGE_ATTR: message[:_MAX_MESSAGE],
                }
            )
            self._call_span.set_status(Status(StatusCode.ERROR, message))

    def mark_tool_api_error(
        self, status: int, message: str, exception: BaseException | None = None
    ) -> None:
        """Call inside a tool when our API fails: puts the HTTP status on the
        `function_tool` span (the SDK only sets `lk.function_tool.is_error`),
        marks that span ERROR, and records a `cadence_error` under it."""
        if not self._enabled:
            return
        span = trace.get_current_span()
        span.set_attributes(
            {
                API_STATUS_CODE_ATTR: status,
                LANGFUSE_LEVEL_ATTR: "ERROR",
                LANGFUSE_STATUS_MESSAGE_ATTR: f"API {status}: {message}"[:_MAX_MESSAGE],
            }
        )
        span.set_status(Status(StatusCode.ERROR, f"API {status}: {message}"))
        self.record_error(
            api_error_attributes("tool", status, message), exception=exception
        )

    def set_call_attributes(self, attributes: dict[str, AttributeValue]) -> None:
        if self._enabled:
            self._call_span.set_attributes(attributes)

    def finish(self) -> None:
        """Set the call's error count; call before ending the call span."""
        self.set_call_attributes({"cadence.errors.count": self.error_count})
