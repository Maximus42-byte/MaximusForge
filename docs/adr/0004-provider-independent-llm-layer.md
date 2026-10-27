# ADR-004: Provider-independent LLM layer

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

MaximusForge must run against API-hosted models (OpenAI, Anthropic, Gemini, ...) and local or
self-hosted ones (Ollama, vLLM, OpenAI-compatible servers). Coupling agents to one vendor SDK
would make the framework hard to evaluate fairly across models and hard to run offline.

## Decision

Agents and nodes depend only on `LLMProvider`, an ABC with a single
`generate(messages, *, options) -> LLMResponse` method, and on framework-owned value types
(`Message`, `Role`, `GenerationOptions`, `LLMResponse`, `TokenUsage`).

- Concrete providers live in adapter modules and translate to and from the vendor wire format.
  Vendor types never cross the interface.
- Providers raise `LLMProviderError`; vendor exceptions are not exposed.
- Credentials come from explicit configuration. `OpenAICompatibleSettings.from_env()` reads
  `MAXIMUSFORGE_LLM_*` variables on request; nothing is read at import time and nothing is
  hard-coded. API keys are held as `SecretStr` and excluded from `repr`.
- The reference adapter targets the OpenAI chat-completions wire format over `httpx`. One
  adapter therefore covers OpenAI and most local servers without any vendor SDK dependency.
- `maximusforge.llm.testing.ScriptedLLMProvider` is a deterministic fake so tests never need
  network access or paid APIs.

## Rationale

A narrow interface is cheap to implement and to fake. Choosing the de-facto OpenAI-compatible
wire format as the first adapter maximises coverage (hosted and local) for minimum code.
An automated test (`tests/test_architecture.py`) rejects imports of vendor SDKs in framework code.

## Consequences

- Vendor-specific features (native tool-calling formats, prompt caching, thinking controls) are
  not available through the interface until they are modelled in a provider-neutral way.
- v0.1 has no streaming and no native tool-calling; those are separate, optional capabilities
  to be added when a node needs them.

## Future considerations

- Streaming responses; structured/JSON-schema output; native tool-calling.
- Native adapters for Anthropic and Gemini where the OpenAI-compatible surface is insufficient.
- Retry/rate-limit policy, cost and latency accounting, and provider routing/fallback.
- An embeddings interface (needed by semantic memory and RAG).
