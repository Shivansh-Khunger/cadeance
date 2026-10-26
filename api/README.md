To install dependencies:
```sh
bun install
```

To run:
```sh
bun run dev
```

open http://localhost:3000

## Env

Bun loads env files from the working directory (`api/`). Locally, `api/.env.local` is a symlink to the root `.env.local`, the file all three apps share. The root `.env` holds remote/prod values; no file in `api/` links to it, so a key missing from `.env.local` stays unset instead of falling back to a prod value. Production doesn't use either file: the hosting platform sets the env. Key names are in the root `.env.example` and `api/.env.example`.

Bun skips `.env.local` when `NODE_ENV=test`. Bun's docs don't say that `--hot` re-reads env files, so restart `bun run dev` after changing `.env.local`.

`bun run db:push` and `bun run db:studio` run drizzle-kit under Node, so `drizzle.config.ts` loads `.env.local` itself with `dotenv`. Today the root `.env` and `.env.local` hold the same `DATABASE_URL`, so `db:push` from your machine changes the shared remote database, not a local one.

## Production (Docker)

`Dockerfile` runs `src/index.ts` with Bun (no build step), production dependencies only, as the image's non-root `bun` user, on port 4000 (`PORT` overrides it). The image reads no env files; set the keys from `.env.example` on the hosting platform: `DATABASE_URL`, `CORS_ORIGIN` (the web app's origin, e.g. the Vercel URL), `LIVEKIT_URL` (the public `wss://` URL, which the API hands to browsers), `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and optionally the three `LANGFUSE_*` keys.

```sh
docker build -t cadence-api .
docker run -p 4000:4000 --env-file ../.env.local cadence-api
```

Building on an Apple Silicon Mac for an x86 host: add `--platform linux/amd64`.

## Tracing (Langfuse)

With `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` all set, `src/telemetry.ts` exports OpenTelemetry spans to `<LANGFUSE_BASE_URL>/api/public/otel/v1/traces`. With any of them unset it does nothing: no provider, no middleware, and `withDbSpan` just runs the query.

- `@hono/otel` makes a server span per request (`POST /appointments`, …, `service.name` `cadence-api`), continuing the trace from the incoming `traceparent`. The agent's `ApiClient` sends one, so in Langfuse a tool call reads `function_tool` → `ApiClient` client span → API route span → DB spans.
- Each DB query in `src/appointments/route.ts` runs inside `withDbSpan(operation, table, query)`, a client span named like `SELECT users`. drizzle 0.45's built-in tracer is inert and postgres.js has no OTel instrumentation, so this is manual. Wrap new queries the same way.
- The agent also sends `X-Cadence-Room: <room name>` on every request, tracing or not. When tracing, every span gets it as `langfuse.session.id`, so API spans land in the call's session.
- Failed queries record the driver's `PostgresError` (message, SQLSTATE in `db.response.status_code`), not drizzle's wrapper, whose message includes the bound parameters.
- Spans include `url.full` with the query string, so phone numbers from `?phone=` reach Langfuse, as they already do through the agent's tool arguments.

Env is read once at startup; `--hot` reloads keep the first provider. Restart `bun run dev` after changing the keys or after `bun install` adds packages.

Bun has no V8 promise hooks, but `AsyncLocalStorage` carries the active span across `await`s, which is all the context manager needs (spike in `.scratch/observability/issues/05-api-db-spans.md`).
