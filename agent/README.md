# Cadence agent (Python)

The voice agent worker for Cadence, built on [LiveKit Agents for Python](https://github.com/livekit/agents) (1.5.x).

It does not use LiveKit Cloud. The worker connects to our self-hosted `livekit-server` (`../infra/livekit/`) and calls the model providers with our own keys (see `../docs/adr/0001-self-host-livekit.md`). STT and TTS are called directly. The LLM goes through [OpenRouter](https://openrouter.ai), a third-party gateway (not LiveKit Cloud or LiveKit Inference), so self-hosting still holds:

- STT: Deepgram Nova-3 (`deepgram` plugin)
- LLM: DeepSeek V4.1 Flash via OpenRouter (`deepseek/deepseek-v4.1-flash-1017`, `openai.LLM.with_openrouter`), with `reasoning_effort="none"` and routing pinned to Fireworks, Together and BaseTen with no fallback
- TTS: Cartesia Sonic-3 (`cartesia` plugin)
- Silero VAD and the local `EnglishModel` turn detector, both running on the worker's CPU
- Evals based on the LiveKit Agents [testing & evaluation framework](https://docs.livekit.io/agents/start/testing/)

Mic noise suppression happens in the browser (WebRTC), not in the agent.

## Using coding agents

This project is designed to work with coding agents like [Claude Code](https://claude.com/product/claude-code), [Cursor](https://www.cursor.com/), and [Codex](https://openai.com/codex/).

For your convenience, LiveKit offers both a CLI and an [MCP server](https://docs.livekit.io/reference/developer-tools/docs-mcp/) that can be used to browse and search its documentation. The [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) (`lk docs`) works with any coding agent that can run shell commands. Install it for your platform:

**macOS:**

```console
brew install livekit-cli
```

**Linux:**

```console
curl -sSL https://get.livekit.io/cli | bash
```

**Windows:**

```console
winget install LiveKit.LiveKitCLI
```

The `lk docs` subcommand requires version 2.15.0 or higher. Check your version with `lk --version` and update if needed. Once installed, your coding agent can search and browse LiveKit documentation directly from the terminal:

```console
lk docs search "voice agents"
lk docs get-page /agents/start/voice-ai-quickstart
```

See the [Using coding agents](https://docs.livekit.io/intro/coding-agents/) guide for more details, including MCP server setup.

The project includes a complete [AGENTS.md](AGENTS.md) file for these assistants. You can modify this file to suit your needs. To learn more about this file, see [https://agents.md](https://agents.md).

## Dev setup

1. Start the local LiveKit server. See `../infra/livekit/README.md` (Docker Compose, ports, macOS notes).
2. Install dependencies:

   ```console
   uv sync
   ```

3. Set up env. The agent loads `.env.local` (`load_dotenv(".env.local")` in `src/agent.py`). In this repo `agent/.env.local` is a symlink to the root `.env.local`, which `api/` and `web/` share. The root `.env` holds remote/prod values and no app links to it, so local runs never read it. Production doesn't use either file; the hosting platform sets the env. Key names are in the root `.env.example`. If you run the agent on its own, copy `agent/.env.example` to `agent/.env.local` instead. The keys:

   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`: the example values (`ws://localhost:7880`, `devkey`, `secret`) match the local server.
   - `DEEPGRAM_API_KEY`, `OPENROUTER_API_KEY`, `CARTESIA_API_KEY`: provider keys. OpenRouter carries only the LLM: it has no streaming STT, and no streaming Cartesia TTS.
   - `API_BASE_URL`: the Hono API the tools call (default `http://localhost:4000`).
   - `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`: optional tracing. Locally they point at the self-hosted Langfuse in `../infra/langfuse`; leave them unset to turn tracing off.

   The worker reads env once, at startup. `dev` auto-reload restarts the worker as a child of the first process, which keeps the env it loaded first (`load_dotenv` doesn't override variables that are already set). After changing `.env.local`, stop `dev` and start it again.

## Run the agent

Before the first run, download the Silero VAD and turn detector models. The turn detector loads its model from disk only and fails to initialise without it:

```console
uv run src/agent.py download-files
```

Then run the worker. Use `dev` locally (auto-reload) and `start` in production:

```console
uv run src/agent.py dev
uv run src/agent.py start
```

The worker registers as `cadence`. The API dispatches it explicitly into each room, so start the API and web app too (see their READMEs).

## Tracing (Langfuse)

With the three `LANGFUSE_*` keys set, each call becomes one Langfuse trace (`src/telemetry.py`): the SDK's spans (`agent_session`, `user_turn`, `eou_detection`, `agent_turn`, `llm_node`, `llm_request`, `function_tool`, `tts_node`, …) under a `cadence_call` root span, exported over OTLP/HTTP to `<LANGFUSE_BASE_URL>/api/public/otel/v1/traces`. Every span carries `langfuse.session.id` = room name, so a call's observations group into one session named after the room. `ApiClient` (`src/api_client.py`) wraps each API request in a client span (`POST /appointments`, …) and sends `traceparent`, so the API's route and DB spans join the same trace under the tool's `function_tool` span (see `../api/README.md`). It also sends `X-Cadence-Room: <room name>` on every request, tracing or not. Errors (STT/LLM/TTS, tool/API 4xx/5xx) appear as `cadence_error` observations at level ERROR, and each LLM call gets an `openrouter_generation` child with the upstream provider, latency and real cost (`src/openrouter_stats.py`). With the keys unset, tracing is off and nothing is exported. Locally, run the Langfuse stack in `../infra/langfuse` and open http://localhost:3100.

LiveKit Agents 1.5.6 can't strip content from spans: transcripts, tool arguments (phone numbers, names) and the full chat context are exported as-is.

## Tests and evals

The agent and the LLM judge both use the OpenRouter-backed LLM, so the tests need `OPENROUTER_API_KEY` only:

```console
uv run pytest
```

CI (`../.github/workflows/agent.yml`, at the repo root because GitHub only runs workflows from there) runs ruff and these tests, reading the key from the `OPENROUTER_API_KEY` repository secret.

## Deploying to production

Build and run the `Dockerfile` next to our own LiveKit server. It runs `download-files` at build time and `start` at run time. Don't use `lk agent deploy`, which targets LiveKit Cloud.

Set `LIVEKIT_URL` (`wss://…` of the production server), its API key/secret, the three provider keys and `API_BASE_URL`. Never set `LIVEKIT_REMOTE_EOT_URL`, `LIVEKIT_INFERENCE_URL` or `LIVEKIT_OBSERVABILITY_URL`: each one makes the worker call LiveKit Cloud hosts.

Server-side production setup (TLS, TURN, ports) is listed under "Production differs" in `../infra/livekit/README.md`.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
