// OpenTelemetry tracing for the API, exported to Langfuse over OTLP/HTTP.
//
// A strict no-op unless LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and
// LANGFUSE_BASE_URL are all set: no provider, no middleware, and
// `withDbSpan` just runs the query.
//
// When enabled, `tracingMiddleware` continues the agent's trace from the
// incoming `traceparent` (sent by agent/src/api_client.py) through
// @hono/otel, so a tool call shows up in Langfuse as
// function_tool -> ApiClient client span -> API route span -> DB spans.
// Bun has no V8 promise hooks, but AsyncLocalStorage carries the active span
// across awaits (ticket 05 spike).
import {
  context,
  createContextKey,
  propagation,
  SpanKind,
  SpanStatusCode,
  trace,
  type Context,
  type Span,
} from '@opentelemetry/api';
import { AsyncLocalStorageContextManager } from '@opentelemetry/context-async-hooks';
import { W3CTraceContextPropagator } from '@opentelemetry/core';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import {
  BasicTracerProvider,
  BatchSpanProcessor,
  type SpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { httpInstrumentationMiddleware } from '@hono/otel';
import { DrizzleQueryError } from 'drizzle-orm';
import type { MiddlewareHandler } from 'hono';

export const SERVICE_NAME = 'cadence-api';
export const ROOM_HEADER = 'X-Cadence-Room';
const LANGFUSE_ENV_VARS = [
  'LANGFUSE_PUBLIC_KEY',
  'LANGFUSE_SECRET_KEY',
  'LANGFUSE_BASE_URL',
] as const;

// Room name from the agent's X-Cadence-Room header, carried in the OTel
// context so every span of the request can be stamped with it.
const ROOM_KEY = createContextKey('cadence.room');

// Same session attributes the agent puts on its spans (agent/src/telemetry.py),
// so API observations land in the call's Langfuse session too.
class RoomSessionProcessor implements SpanProcessor {
  onStart(span: Span, parentContext: Context): void {
    const room = parentContext.getValue(ROOM_KEY);
    if (typeof room === 'string' && room) {
      span.setAttribute('langfuse.session.id', room);
      span.setAttribute('lk.room_name', room);
    }
  }
  onEnd(): void {}
  forceFlush(): Promise<void> {
    return Promise.resolve();
  }
  shutdown(): Promise<void> {
    return Promise.resolve();
  }
}

export function langfuseOtlpConfig(publicKey: string, secretKey: string, baseUrl: string) {
  const auth = Buffer.from(`${publicKey}:${secretKey}`).toString('base64');
  return {
    url: `${baseUrl.replace(/\/+$/, '')}/api/public/otel/v1/traces`,
    headers: {
      Authorization: `Basic ${auth}`,
      // Without this, Langfuse v4 can take minutes to show spans.
      'x-langfuse-ingestion-version': '4',
    },
  };
}

// `bun run --hot` re-evaluates modules but keeps globalThis, and OTel's global
// registration is once per process, so keep the provider there.
const STATE = Symbol.for('cadence.api.telemetry');
type State = { provider: BasicTracerProvider | null };
const g = globalThis as typeof globalThis & { [STATE]?: State };

function setupTelemetry(): BasicTracerProvider | null {
  const [publicKey, secretKey, baseUrl] = LANGFUSE_ENV_VARS.map((k) => process.env[k]);
  if (!publicKey || !secretKey || !baseUrl) return null;

  const provider = new BasicTracerProvider({
    resource: resourceFromAttributes({ 'service.name': SERVICE_NAME }),
    spanProcessors: [
      new RoomSessionProcessor(),
      new BatchSpanProcessor(
        new OTLPTraceExporter(langfuseOtlpConfig(publicKey, secretKey, baseUrl)),
      ),
    ],
  });
  context.setGlobalContextManager(new AsyncLocalStorageContextManager().enable());
  propagation.setGlobalPropagator(new W3CTraceContextPropagator());
  trace.setGlobalTracerProvider(provider);
  // No SIGINT/SIGTERM flush hook: a signal listener would stop Ctrl-C from
  // exiting the server. At most the last ~5 s batch is lost on shutdown.
  return provider;
}

const state: State = (g[STATE] ??= { provider: setupTelemetry() });
export const telemetryEnabled = state.provider !== null;

const tracer = trace.getTracer(SERVICE_NAME);

/**
 * Hono middleware: a server span per request, child of the caller's
 * `traceparent`, tagged with the X-Cadence-Room session. Passes straight
 * through when telemetry is off.
 */
export function tracingMiddleware(): MiddlewareHandler {
  if (!telemetryEnabled) return (_c, next) => next();
  const otel = httpInstrumentationMiddleware({ serviceName: SERVICE_NAME });
  return (c, next) => {
    const room = c.req.header(ROOM_HEADER);
    if (!room) return otel(c, next);
    return context.with(context.active().setValue(ROOM_KEY, room), () => otel(c, next));
  };
}

/**
 * Runs one DB query in a client span named `<operation> <table>`, e.g.
 * `SELECT users`. drizzle 0.45's own tracer is inert and postgres.js has no
 * OTel instrumentation, so the queries are wrapped by hand.
 */
export async function withDbSpan<T>(
  operation: 'SELECT' | 'INSERT' | 'UPDATE' | 'DELETE',
  table: string,
  query: () => PromiseLike<T>,
): Promise<T> {
  if (!telemetryEnabled) return query();
  return tracer.startActiveSpan(
    `${operation} ${table}`,
    {
      kind: SpanKind.CLIENT,
      attributes: {
        'db.system.name': 'postgresql',
        'db.operation.name': operation,
        'db.collection.name': table,
      },
    },
    async (span) => {
      try {
        const result = await query();
        if (Array.isArray(result)) span.setAttribute('db.response.returned_rows', result.length);
        return result;
      } catch (err) {
        recordError(span, err);
        throw err;
      } finally {
        span.end();
      }
    },
  );
}

/**
 * Marks `span` failed with `err`; used by `withDbSpan` and `app.onError`.
 * drizzle wraps driver errors as "Failed query: <sql>\nparams: <values>", so
 * record its cause (postgres.js `PostgresError`: the real reason, SQLSTATE in
 * `code`, no bound values such as phone numbers) instead.
 */
export function recordError(span: Span, err: unknown) {
  const e = err instanceof DrizzleQueryError && err.cause instanceof Error ? err.cause : err;
  if (e instanceof Error) {
    span.recordException(e);
    span.setAttribute('error.type', e.name);
    const code = (e as { code?: unknown }).code;
    if (typeof code === 'string') span.setAttribute('db.response.status_code', code);
  }
  span.setStatus({ code: SpanStatusCode.ERROR, message: e instanceof Error ? e.message : String(e) });
}
