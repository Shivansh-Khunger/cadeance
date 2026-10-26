import { trace } from '@opentelemetry/api';
import { Hono } from 'hono'
import { cors } from 'hono/cors'
import { HTTPException } from 'hono/http-exception'
import { adminRouter } from './admin/route';
import { appointmentsRouter } from './appointments/route';
import { env } from './config/env';
import { livekit } from './livekit/route';
import { connectDB } from './config/database';
import { recordError, tracingMiddleware } from './telemetry';

const app = new Hono()
connectDB()

// First, so the route span covers every other middleware. A pass-through
// unless the LANGFUSE_* vars are set (see ./telemetry.ts).
app.use('*', tracingMiddleware());

app.use('*', cors({
  origin: env.CORS_ORIGIN,
  allowMethods: ['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
  allowHeaders: ['Content-Type', 'Authorization'],
}));

// Catch-all for anything a route didn't handle (e.g. DB down, missing tables).
// Logs the real error and keeps the `{ error }` shape the agent's ApiClient
// relays to the LLM, instead of Hono's bare "Internal Server Error" text.
app.onError((err, c) => {
  if (err instanceof HTTPException) return err.getResponse();
  // The active span is the request's route span (a no-op span when tracing
  // is off).
  const span = trace.getActiveSpan();
  if (span) recordError(span, err);
  console.error(`${c.req.method} ${c.req.path} failed`, err);
  return c.json({ error: 'Internal server error, please try again shortly' }, 500);
});

app.get('/', (c) => {
  return c.text('Hello Hono!')
})

app.route('/', livekit);
app.route('/', appointmentsRouter);
app.route('/admin', adminRouter);

export default {
  port: env.PORT,
  fetch: app.fetch,
};
