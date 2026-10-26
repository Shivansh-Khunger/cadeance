# Local LiveKit server

A self-hosted, open-source `livekit-server` (pinned to `v1.13.10`) for local
development. The agent worker, the API and the web client all talk to it
instead of LiveKit Cloud. Single node, no Redis: agent dispatch works without it.

## Start / stop

From the repo root (Docker Desktop must be running):

```sh
docker compose -f infra/livekit/docker-compose.yml up -d      # start
docker compose -f infra/livekit/docker-compose.yml logs -f    # follow logs
docker compose -f infra/livekit/docker-compose.yml down       # stop
```

The server is up when the log shows
`starting LiveKit server {"portHttp": 7880, ..., "nodeIP": "127.0.0.1", ...}`.

Smoke test with the LiveKit CLI:

```sh
lk room list --url http://localhost:7880 --api-key devkey --api-secret secret
```

Run `lk` outside `agent/` (or pass the flags above), so it does not pick up a
Cloud project config.

## Env values for each app

| App | File | Values |
| --- | --- | --- |
| Agent worker | `agent/.env.local` | `LIVEKIT_URL=ws://localhost:7880`, `LIVEKIT_API_KEY=devkey`, `LIVEKIT_API_SECRET=secret` |
| API (token minting) | `api/.env` | same three values (see `api/.env.example`) |
| Web client | none | gets `server_url` and the token from the API's `/connection-details` response |

`devkey` / `secret` is LiveKit's well-known dev key pair. It is fine on
loopback only. The compose file publishes every port on `127.0.0.1`, so nothing
is reachable from your LAN.

Before the first `uv run src/agent.py dev`, run `uv run src/agent.py download-files`
once in `agent/`. Otherwise the turn-detector model is missing and the worker
logs `Could not find file "model_q8.onnx"`.

## Ports

| Port | Use |
| --- | --- |
| 7880/tcp | signal WebSocket + HTTP API (`ws://localhost:7880`) |
| 7881/tcp | ICE over TCP (fallback) |
| 50000-50100/udp | ICE over UDP (`rtc.port_range_start/end`) |

## macOS / Docker Desktop notes

- The container's IP (172.x) is not reachable from the Mac, so `livekit.yaml`
  sets `rtc.node_ip: 127.0.0.1` and `rtc.use_external_ip: false`. The server
  then advertises `127.0.0.1:<udp port>` ICE candidates, and Docker Desktop
  forwards them into the container. This was checked with the Python agent and
  headless Chrome: both picked a `udp4 host 127.0.0.1:500xx` pair.
- The UDP range is small (101 ports) because Docker Desktop is slow to publish
  big ranges. Each participant uses up to two ports (publisher + subscriber), so
  this is plenty for local calls. If you widen it, change `livekit.yaml` and
  `docker-compose.yml` together.
- Because `node_ip` is `127.0.0.1`, only browsers on this Mac can connect. To
  test from a phone on your LAN you would need the Mac's LAN IP as `node_ip`
  and ports published on `0.0.0.0`. Don't do that with the dev key pair.
- `development: true` in `livekit.yaml` only silences the "secret is too
  short" error for `secret` and enables `/debug/pprof`. Because `keys` is set
  explicitly, the server still binds to all container interfaces.

## Production differs

This setup is for local dev only. Production (ticket 05,
`.scratch/self-host-livekit/issues/05-production-hosting.md`) needs:

- a domain with a trusted TLS certificate (`wss://…`; self-signed does not work),
- `rtc.use_external_ip: true` (or the real public `node_ip`) instead of `127.0.0.1`,
- the embedded TURN server (TURN/TLS on 443 or 5349) for clients behind strict firewalls,
- a wider UDP range (default 50000-60000) and host networking,
- real API keys with secrets of at least 32 characters, never `devkey` / `secret`,
- `development: false`.

See https://docs.livekit.io/transport/self-hosting/deployment/ and
https://github.com/livekit/livekit/blob/master/config-sample.yaml.
