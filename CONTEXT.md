# Cadence

Voice agent for a healthcare clinic's front desk: callers identify themselves by phone number and book, reschedule, look up, or cancel appointments.

## Language

### Conversation

**Cadence**:
The voice agent itself, as a persona and as the running pipeline.
_Avoid_: bot, assistant

**Caller**:
The person speaking with Cadence during a call, identified by phone number.
_Avoid_: user (overloaded with the `users` record), customer, patient

**Turn**:
One caller utterance followed by Cadence's spoken response.

**Tool turn**:
A turn in which Cadence calls at least one tool before (or while) responding.

### Performance

**Voice-to-voice latency**:
Time from the end of the caller's speech to the start of Cadence's speech, as heard at the client (per voiceaiandvoiceagents.com). Reported as p50/p95.
_Avoid_: latency (unqualified), response time, TTFT

**Latency budget**:
The breakdown of voice-to-voice latency into per-component contributions (endpointing, STT, LLM time-to-first-token, TTS time-to-first-byte, avatar, transport, tool round-trips).

**Avatar mode** / **Audio-only mode**:
The two delivery modes of Cadence: with the Beyond Presence video avatar in the output path, or speech only. Latency is reported per mode.

**Synthetic caller**:
A scripted participant that joins a room, plays pre-recorded caller audio, and records Cadence's replies so voice-to-voice latency can be measured repeatably.
_Avoid_: test bot, fake user

**Agent harness**:
Everything around the models that shapes a turn: instructions, tool design, context management, text/audio transforms, and session settings.
_Avoid_: harness (unqualified; ambiguous with the measurement rig)

**Guardrail behaviour**:
A booking-flow behaviour that must not regress when optimizing latency (phone confirmation, slow read-back before mutating tools, multi-intent ordering, clean turn-taking).
