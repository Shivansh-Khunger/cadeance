---
name: livekit-agents
description: 'Build and change the Cadence voice agent on the LiveKit Agents SDK, self-hosted: our own livekit-server plus Deepgram and Cartesia plugins and an OpenRouter-backed LLM, never LiveKit Cloud or LiveKit Inference. Use when the user asks to "build a voice agent", "add voice AI", add tools, "implement handoffs", "structure agent workflows", change STT/LLM/TTS, turn detection or interruptions, or is working with LiveKit Agents SDK. REQUIRES writing tests for all implementations.'
license: MIT
metadata:
  author: livekit (adapted for self-hosting)
  version: "0.3.0-selfhost"
---

# LiveKit Agents Development, Self-Hosted

This skill encodes *how to approach* voice agent development in this repo, not API specifics. All factual information about APIs, methods, and configurations must come from live documentation.

This repo runs the open-source `livekit-server` itself (`infra/livekit/`) and calls the model providers with our own keys: Deepgram STT and Cartesia TTS directly through their plugins, and the LLM (DeepSeek V4.1 Flash) through OpenRouter via `openai.LLM.with_openrouter`. It never uses LiveKit Cloud, LiveKit Inference (`inference.*` classes or model strings like `"deepgram/nova-3"`), Cloud agent hosting (`lk agent deploy`, `livekit.toml`), or Krisp/Cloud noise cancellation. The why is in `docs/adr/0001-self-host-livekit.md`.

## MANDATORY: Read This Checklist Before Starting

Before writing ANY code, complete this checklist:

1. **Read this entire skill document** - Do not skip sections even if MCP is available
2. **Have the self-hosted stack running** - Local server up and `agent/.env.local` filled in (see the rules below)
3. **Set up documentation access** - Use the `lk docs` CLI or MCP if available, otherwise use web search
4. **Plan to write tests** - Every agent implementation MUST include tests (see testing section below)
5. **Verify all APIs against live docs** - Never rely on model memory for LiveKit APIs

This checklist applies regardless of whether MCP is available. MCP provides documentation access but does NOT replace the guidance in this skill.

## Self-Hosting Rules

The live docs default to LiveKit Cloud + LiveKit Inference in their examples. Translate every example to the self-hosted setup with these rules:

- **Local server:** start it as `infra/livekit/README.md` describes.
- **Env keys:** `agent/.env.example` lists every key the worker needs (`DEEPGRAM_API_KEY`, `OPENROUTER_API_KEY`, `CARTESIA_API_KEY`, plus the LiveKit server pair). Copy it to `.env.local`.
- **OpenRouter:** it is a third-party gateway, not LiveKit Cloud or LiveKit Inference, so using it keeps us self-hosted. It carries the LLM only. Keep STT and TTS on direct plugins unless OpenRouter gains a streaming STT, or a streaming TTS that keeps the Cartesia inline tags; its `/audio/speech` and `/audio/transcriptions` endpoints are request/response, which would add whole-utterance latency to every turn. Keep the model pinned (`provider` routing in `build_agent_llm`) and reasoning off.
- **Run:** `uv run src/agent.py download-files` once (the turn detector loads its model from disk only), then `dev` locally or `start` in production.
- **Interruptions:** keep `interruption={"mode": "vad"}` pinned in the session's turn handling. Any other mode, or leaving it unset in `dev`, calls adaptive interruption on the Cloud gateway.
- **Turn detector:** keep the local `MultilingualModel`. On an upgrade to its successor, pin the mini/local variant (`v1-mini`): the full model runs on LiveKit Inference.
- **Worker env:** leave `LIVEKIT_REMOTE_EOT_URL`, `LIVEKIT_INFERENCE_URL` and `LIVEKIT_OBSERVABILITY_URL` unset. Each one makes the worker call LiveKit-operated hosts.
- **New model or provider:** for an LLM, change `AGENT_MODEL` to another OpenRouter id and re-check its reasoning-off switch, tool support and provider pinning against `openrouter.ai/api/v1/models/<id>/endpoints`. For STT/TTS, add the plugin extra to `livekit-agents[...]` in `agent/pyproject.toml`, construct the plugin class, and add its key to `agent/.env.example`. Inference model strings are not an option.
- **New SDK feature:** before using it, check the docs and the installed source for calls to `agent-gateway.livekit.cloud`. Known cases: the audio `inference.TurnDetector` and adaptive interruption. A feature that routes there is off-limits.
- **SDK version:** the docs track the newest release; this repo pins `livekit-agents` in `agent/pyproject.toml`. When they disagree, the installed source in `agent/.venv` wins.
- **Observability:** Cloud Agent Observability (session reports, traces) is unavailable. The SDK's OpenTelemetry spans go to Langfuse instead, through `src/telemetry.py` (`livekit.agents.telemetry.set_tracer_provider` with our own OTLP exporter, set inside the job); see the README's Tracing section. Also use worker logs and `session.usage`.

## Critical Rule: Never Trust Model Memory for LiveKit APIs

LiveKit Agents is a fast-evolving SDK. Model training data is outdated the moment it's created. When working with LiveKit:

- **Never assume** API signatures, method names, or configuration options from memory
- **Never guess** SDK behavior or default values
- **Always verify** against live documentation before writing code
- **Always cite** the documentation source when implementing features

This rule applies even when confident about an API. Verify anyway.

## REQUIRED: Use Live LiveKit Documentation

Before writing any LiveKit code, ensure access to live LiveKit documentation. This provides current, verified API information and prevents reliance on stale model knowledge.

### Check for the CLI or MCP

Use the `lk docs` CLI (commands and install steps in `agent/AGENTS.md`) or the `livekit-docs` MCP tools, whichever is available, for all documentation lookups:
- Search documentation before implementing any feature
- Verify API signatures and method parameters
- Look up configuration options and their valid values
- Find working examples for the specific task at hand

### If Neither Is Available

If neither the CLI nor the MCP server is set up, inform the user and recommend installation. Installation instructions for all supported platforms are available at:

**https://docs.livekit.io/intro/mcp-server/**

Fetch the installation instructions appropriate for the user's coding agent from that page.

### Fallback When Neither Is Available

If neither can be installed in the current session:
1. **Inform the user immediately** that documentation cannot be verified in real-time
2. Use web search to fetch current documentation from docs.livekit.io
3. **Explicitly mark all LiveKit-specific code** with a comment like `# UNVERIFIED: Please check docs.livekit.io for current API`
4. **State clearly** when you cannot verify something: "I cannot verify this API signature against current documentation"
5. Recommend the user verify against https://docs.livekit.io before using the code

## Voice Agent Architecture Principles

Voice AI agents have fundamentally different requirements than text-based agents or traditional software. Internalize these principles:

### Latency Is Critical

Voice conversations are real-time. Users expect responses within hundreds of milliseconds, not seconds. Every architectural decision should consider latency impact:

- Minimize LLM context size to reduce time to first token
- Avoid unnecessary tool calls during active conversation
- Prefer streaming responses over batch responses
- Design for the unhappy path (network delays, API timeouts)

### Context Bloat Kills Performance

Large system prompts and extensive tool lists directly increase latency. A voice agent with 50 tools and a 10,000-token system prompt will feel sluggish regardless of model speed.

Design agents with minimal viable context:
- Include only tools relevant to the current conversation phase
- Keep system prompts focused and concise
- Remove tools and context that aren't actively needed

### Users Don't Read, They Listen

Voice interface constraints differ from text:
- Long responses frustrate users—keep outputs concise
- Users cannot scroll back—ensure clarity on first delivery
- Interruptions are normal—design for graceful handling
- Silence feels broken—acknowledge processing when needed

## Workflow Architecture: Handoffs and Tasks

Complex voice agents should not be monolithic. LiveKit Agents supports structured workflows that maintain low latency while handling sophisticated use cases.

### The Problem with Monolithic Agents

A single agent handling an entire conversation flow accumulates:
- Tools for every possible action (bloated tool list)
- Instructions for every conversation phase (bloated context)
- State management for all scenarios (complexity)

This creates latency and reduces reliability.

### Handoffs: Agent-to-Agent Transitions

Handoffs allow one agent to transfer control to another. Use handoffs to:
- Separate distinct conversation phases (greeting → intake → resolution)
- Isolate specialized capabilities (general support → billing specialist)
- Manage context boundaries (each agent has only what it needs)

Design handoffs around natural conversation boundaries where context can be summarized rather than transferred wholesale.

### Tasks: Scoped Operations

Tasks are tightly-scoped prompts designed to achieve a specific outcome. Use tasks for:
- Discrete operations that don't require full agent capabilities
- Situations where a focused prompt outperforms a general-purpose agent
- Reducing context when only a specific capability is needed

Consult the documentation for implementation details on handoffs and tasks.

## REQUIRED: Write Tests for Agent Behavior

Voice agent behavior is code. Every agent implementation MUST include tests. Shipping an agent without tests is shipping untested code.

### Mandatory Testing Workflow

When building or modifying a LiveKit agent:

1. **Create a `tests/` directory** if one doesn't exist
2. **Write at least one test** before considering the implementation complete
3. **Test the core behavior** the user requested
4. **Run the tests** to verify they pass

A minimal test file structure:
```
project/
├── agent.py (or src/agent.py)
└── tests/
    └── test_agent.py
```

### Test-Driven Development Process

When modifying agent behavior—instructions, tool descriptions, workflows—begin by writing tests for the desired behavior:

1. Define what the agent should do in specific scenarios
2. Write test cases that verify this behavior
3. Implement the feature
4. Iterate until tests pass

This approach prevents shipping agents that "seem to work" but fail in production.

### What Every Agent Test Should Cover

At minimum, write tests for:
- **Basic conversation flow**: Agent responds appropriately to a greeting
- **Tool invocation** (if tools exist): Tools are called with correct parameters
- **Error handling**: Agent handles unexpected input gracefully

Focus tests on:
- **Tool invocation**: Does the agent call the right tools with correct parameters?
- **Response quality**: Does the agent produce appropriate responses for given inputs?
- **Workflow transitions**: Do handoffs and tasks trigger correctly?
- **Edge cases**: How does the agent handle unexpected input, interruptions, silence?

### Test Implementation Pattern

Use LiveKit's testing framework. Consult the testing documentation for current patterns:
```
search: "livekit agents testing"
```

The framework supports:
- Simulated user input
- Verification of agent responses
- Tool call assertions
- Workflow transition testing

### Why This Is Non-Negotiable

Agents that "seem to work" in manual testing frequently fail in production:
- Prompt changes silently break behavior
- Tool descriptions affect when tools are called
- Model updates change response patterns

Tests catch these issues before users do.

### Skipping Tests

If a user explicitly requests no tests, proceed without them but inform them:
> "I've built the agent without tests as requested. I strongly recommend adding tests before deploying to production. Voice agents are difficult to verify manually and tests prevent silent regressions."

## Common Mistakes to Avoid

### Overloading the Initial Agent

Starting with one agent that "does everything" and adding tools/instructions over time. Instead, design workflow structure upfront, even if initial implementation is simple.

### Ignoring Latency Until It's a Problem

Latency issues compound. An agent that feels "a bit slow" in development becomes unusable in production with real network conditions. Measure and optimize latency continuously.

### Copying Examples Without Understanding

Examples in documentation demonstrate specific patterns. Copying code without understanding its purpose leads to bloated, poorly-structured agents. Understand what each component does before including it.

### Skipping Tests Because "It's Just Prompts"

Agent behavior is code. Prompt changes affect behavior as much as code changes. Test agent behavior with the same rigor as traditional software. **Never deliver an agent implementation without at least one test file.**

### Assuming Model Knowledge Is Current

Reiterating the critical rule: never trust model memory for LiveKit APIs. The SDK evolves faster than model training cycles. Verify everything.

## When to Consult Documentation

**Always consult documentation for:**
- API method signatures and parameters
- Configuration options and their valid values
- SDK version-specific features or changes
- Deployment and infrastructure setup (this repo's own setup: the files named in Self-Hosting Rules)
- Model provider integration details
- CLI commands and flags

**This skill provides guidance on:**
- Architectural approach and design principles
- Workflow structure decisions
- Testing strategy
- Common pitfalls to avoid

The distinction matters: this skill tells you *how to think* about building voice agents. The documentation tells you *how to implement* specific features.

## Feedback Loop

When using LiveKit documentation via the CLI or MCP, note any gaps, outdated information, or confusing content. Reporting documentation issues helps improve the ecosystem for all developers.

## Summary

Building effective voice agents in this repo requires:

1. **Stay self-hosted**: our own LiveKit server, direct STT/TTS plugins and the OpenRouter LLM, per the Self-Hosting Rules
2. **Verify everything** against live documentation—never trust model memory
3. **Minimize latency** at every architectural decision point
4. **Structure workflows** using handoffs and tasks to manage complexity
5. **Test behavior** before and after changes—never ship without tests
6. **Keep context minimal**—only include what's needed for the current phase

These principles remain valid regardless of SDK version or API changes. For all implementation specifics, consult the live LiveKit documentation.