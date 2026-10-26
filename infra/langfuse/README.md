# Local Langfuse

A self-hosted Langfuse v4 (pinned to `4.70.0`) for local development. Local
runs of the agent send their OpenTelemetry traces here instead of to Langfuse
Cloud. Production uses Langfuse Cloud (JP region) with the keys in the root
`.env`; see `.scratch/observability/spec.md`.

The compose file is Langfuse's official
[`docker-compose.yml`](https://github.com/langfuse/langfuse/blob/v4.70.0/docker-compose.yml)
with pinned images, loopback-only ports, secrets moved to `.env`, and
healthchecks. The header of `docker-compose.yml` lists every change.

## Services

| Service | Image | Role |
| --- | --- | --- |
| `langfuse-web` | `docker.langfuse.com/langfuse/langfuse:4.70.0` | UI + public API (OTLP ingest) |
| `langfuse-worker` | `docker.langfuse.com/langfuse/langfuse-worker:4.70.0` | async ingestion into ClickHouse |
| `postgres` | `postgres:17.11` | users, orgs, projects, API keys |
| `clickhouse` | `clickhouse/clickhouse-server:25.12.11.4` | traces and observations |
| `redis` | `redis:7.4.11` | queue + cache |
| `minio` | `cgr.dev/chainguard/minio@sha256:87896cb3…` | S3 blob storage (raw events, media) |

Chainguard publishes MinIO only as `latest`, so it is pinned by digest.

## First-time setup

Create `infra/langfuse/.env` (gitignored) from the template and fill in every
`<...>` by running the command inside it:

```sh
cp infra/langfuse/.env.example infra/langfuse/.env
```

The `LANGFUSE_INIT_*` values there create the org, project, API keys and login
on first start ([headless initialization](https://langfuse.com/self-hosting/administration/headless-initialization)).
They only apply when those resources don't exist yet. To change them later,
reset the volumes (below).

Then copy the project keys into the root `.env.local`, which local runs read:

```sh
LANGFUSE_PUBLIC_KEY=<LANGFUSE_INIT_PROJECT_PUBLIC_KEY>
LANGFUSE_SECRET_KEY=<LANGFUSE_INIT_PROJECT_SECRET_KEY>
LANGFUSE_BASE_URL=http://localhost:3100
```

## Start / stop

From the repo root (Docker Desktop must be running):

```sh
docker compose -f infra/langfuse/docker-compose.yml up -d --wait   # start, wait for healthy
docker compose -f infra/langfuse/docker-compose.yml ps             # all six should say (healthy)
docker compose -f infra/langfuse/docker-compose.yml logs -f langfuse-web
docker compose -f infra/langfuse/docker-compose.yml down           # stop, keep data
```

Compose reads `infra/langfuse/.env` on its own because it sits next to the
compose file. If it is missing, `up` fails with `... set in infra/langfuse/.env`.

The first start runs the Postgres and ClickHouse migrations and takes about a
minute. The stack is up when `curl http://localhost:3100/api/public/health`
returns `{"status":"OK","version":"4.70.0"}`.

## UI and login

- UI: <http://localhost:3100>
- Login: `dev@cadence.local` (`LANGFUSE_INIT_USER_EMAIL`), password in
  `LANGFUSE_INIT_USER_PASSWORD` in `infra/langfuse/.env`.
- Org `Cadence` (`cadence`), project `cadence-local`.

## Sending traces

OTLP over HTTP only (no gRPC), per
<https://langfuse.com/integrations/native/opentelemetry>:

| Setting | Value |
| --- | --- |
| Endpoint | `http://localhost:3100/api/public/otel` (traces: `.../otel/v1/traces`) |
| `Authorization` | `Basic base64(LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY)` |
| `x-langfuse-ingestion-version` | `4` (without it, spans can take minutes to appear) |

v4 has dropped the legacy read endpoints (`GET /api/public/traces/{id}` returns
"not available ... in Langfuse v4 events_only mode"). To check from the shell
that a trace arrived, use the v2 observations API:

```sh
curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "http://localhost:3100/api/public/v2/observations?traceId=<32-hex trace id>&fields=core,basic"
```

## Ports

Everything is published on `127.0.0.1` only.

| Port | Use |
| --- | --- |
| 3100/tcp | UI + public API (`langfuse-web`, container port 3000) |
| 9090/tcp | MinIO S3 API: the browser loads media and batch-export downloads through it |

The worker (3030), MinIO console (9001), Postgres (5432), ClickHouse (8123,
9000) and Redis (6379) are reachable only inside the compose network. Upstream
publishes them on loopback; we don't, to avoid clashing with other local
databases. To poke at one, use `docker compose ... exec`, for example
`docker compose -f infra/langfuse/docker-compose.yml exec postgres psql -U postgres`.

3000 is the web app and 4000 the API; LiveKit uses 7880, 7881 and UDP
50000-50100 (`infra/livekit/`).

## Data and reset

Data lives in named Docker volumes (compose project `langfuse`):
`langfuse_langfuse_postgres_data`, `langfuse_langfuse_clickhouse_data`,
`langfuse_langfuse_clickhouse_logs`, `langfuse_langfuse_minio_data`,
`langfuse_langfuse_redis_data`. `down` keeps them.

To wipe all traces, users and keys and start fresh (headless init runs again on
the next `up`, so you can change `LANGFUSE_INIT_*` first):

```sh
docker compose -f infra/langfuse/docker-compose.yml down -v
```

## Memory

Langfuse recommends 4 cores and 16 GiB for a VM. Locally, after start-up and one
test trace, `docker stats` showed about 3.0 GiB for the stack:

| Container | Memory |
| --- | --- |
| langfuse-web | ~1.1 GiB |
| clickhouse | ~1.1 GiB |
| langfuse-worker | ~630 MiB |
| minio | ~90 MiB |
| postgres | ~65 MiB |
| redis | ~25 MiB |

Docker Desktop's VM on this Mac has 7.75 GiB. That fits next to LiveKit, but
stop Langfuse (`down`) when you don't need traces. ClickHouse grows with data.

## Upgrading

Bump both Langfuse tags together, check the release notes
(<https://github.com/langfuse/langfuse/releases>) and diff the new upstream
`docker-compose.yml` against ours. Then:

```sh
docker compose -f infra/langfuse/docker-compose.yml up -d --wait
```

Migrations run on start. Keep `CLICKHOUSE` on the version upstream's compose
file uses.

## Production differs

Production sends traces to Langfuse Cloud (JP), not to this stack. This setup
has no backups, no high availability and loopback-only ports, and it is not
meant to be exposed.
