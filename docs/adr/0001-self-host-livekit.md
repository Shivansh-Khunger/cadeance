# 0001: Self-host LiveKit, no LiveKit Cloud

## Status

Accepted, 2026-10-25.

## Context

Cadence was built on the LiveKit Cloud starter and depended on Cloud in several places:

- `agent/livekit.toml` held a Cloud agent-hosting config, and the `.env` files pointed at a `*.livekit.cloud` project.
- The agent's STT, LLM and TTS went through LiveKit Inference (`inference.STT/LLM/TTS`), a gateway billed through the Cloud project. The tests used it too, and CI passed Cloud secrets for it.
- The web client used the Cloud-only Krisp noise filter (`@livekit/krisp-noise-filter`).
- livekit-agents 1.5.6 also calls Cloud on its own: adaptive interruption detection in `dev` mode, and the remote turn detector, Inference and observability endpoints when their env vars (or a Cloud `LIVEKIT_URL`) are set.

Silero VAD, the `MultilingualModel` turn detector, explicit dispatch (`agent_name="cadence"`) and token minting already worked on the open-source server.

## Decision

Decided by the human on 2026-10-25: Cadence does not depend on LiveKit Cloud at all.

- We run the open-source `livekit-server` ourselves. Local dev uses `infra/livekit/` (see its README); production runs on a VM we operate.
- The worker calls providers directly with our own keys: Deepgram Nova-3 (STT), xAI `grok-4.3` with `reasoning_effort="none"` (LLM; `grok-4-1-fast-non-reasoning` was retired on 2026-05-15) and Cartesia Sonic-3 (TTS).
- The agent runs from `agent/Dockerfile` (`start`), not Cloud agent hosting.
- The browser's WebRTC noise suppression, echo cancellation and auto gain replace Krisp.

## Consequences

- We operate the media server ourselves: TLS, TURN for callers behind strict NATs, firewall/UDP ranges, upgrades. Scaling past one node needs Redis.
- We lose Krisp. Browser noise suppression is the replacement. Server-side ai-coustics with our own licence is a later option if STT accuracy suffers.
- We lose LiveKit Inference, the Cloud audio turn detector (`inference.TurnDetector`) and Cloud agent observability (session reports, traces).
- We pay Deepgram, xAI and Cartesia directly. `agent/src/cost.py` prices changed with the model swap: grok-4.3 is $1.25/$2.50 per Mtok (was $0.20/$0.50), Deepgram multilingual streaming is $0.0058/min (promo rate).
- Interruptions are pinned to VAD mode (`interruption={"mode": "vad"}`), so adaptive backchannel filtering, which only runs on LiveKit Inference, is lost: a caller's "mm-hmm" while Cadence speaks counts as an interruption. The local option is tuning `min_duration` / `min_words`. Whether that is acceptable is still an open human decision.
- The turn detector is the local `MultilingualModel`, which runs offline on the worker's CPU (models fetched by `download-files`). It is deprecated for livekit-agents 2.0. Its replacement runs its full model on LiveKit Inference, so on upgrade pin the mini/local variant (`v1-mini`) or keep `MultilingualModel`.
- The production worker must never set `LIVEKIT_REMOTE_EOT_URL`, `LIVEKIT_INFERENCE_URL` or `LIVEKIT_OBSERVABILITY_URL`. Each one makes livekit-agents call LiveKit-operated hosts. Its `LIVEKIT_URL` must not be a `*.livekit.cloud` host either.

## Update (2026-10-25)

The LLM is now DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash-1017`) through OpenRouter, routed to Fireworks, Together and BaseTen with no fallback, and STT is Deepgram Nova-3 `en` instead of `multi`. The self-hosting decision is unchanged; the model and cost lines above describe the state when this ADR was written. Current rates are in `agent/src/cost.py`.
