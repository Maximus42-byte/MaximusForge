# Roadmap

Status legend: ✅ available · 🚧 in development · 🗓️ planned. Phases are ordered by dependency,
not by dates; scope within a phase may change as we learn.

## Phase 0 — Bootstrap v0.1 ✅ (this repository state)

- ✅ Package layout, packaging, lint/type/test configuration
- ✅ Core abstractions: `Task`, `AgentState`, `Agent`, `Workflow`, `WorkflowNode`, `WorkflowEngine`
- ✅ LangGraph adapter behind the engine boundary
- ✅ `LLMProvider` + OpenAI-compatible reference adapter
- ✅ `Tool` / `ToolRegistry`
- ✅ `MemoryStore` / `SemanticMemoryStore` interfaces + non-durable `InMemoryStore`
- ✅ `Planner` / `LLMPlanner`
- ✅ Minimal `ResearchAgent` (architecture check, not a product)
- ✅ ADRs 0001–0006

## Phase 1 — Robust execution 🗓️

- Retry policies and backoff per node
- Error routing (failure-handling edges), re-planning on failure
- Execution events/hooks and structured logging (foundation for observability)
- Native LLM tool-calling and a bounded `ReActNode`
- Streaming responses
- Serializable state contract

## Phase 2 — Memory and retrieval 🗓️

- PostgreSQL + pgvector `SemanticMemoryStore`, migrations
- Embedding provider abstraction
- RAG pipeline: ingestion, chunking, retrieval, citation tracking
- Memory retention and deletion controls

## Phase 3 — Persistence and control 🗓️

- Checkpointed, resumable workflow execution
- Human-in-the-loop interrupt/resume
- Parallel branches and joins
- Additional providers (native Anthropic, Gemini; Ollama/vLLM configuration guides)

## Phase 4 — Flagship agents 🗓️

1. Research Assistant Agent (grown from the current skeleton)
2. Code Review Agent
3. AI Security Agent
4. Membership Inference Audit Agent
5. Agent Evaluation Platform

## Phase 5 — Ecosystem and platform 🗓️

- MCP integration
- Multi-agent orchestration
- Evaluation and benchmarking harness
- Observability (tracing, metrics)
- AI security and privacy evaluation tooling
- CI/CD, containerisation and deployment infrastructure — introduced when they have something
  real to build and ship

## Deferred on purpose

Docker, PostgreSQL/Redis services, CI, Kubernetes and OpenTelemetry are not part of the
bootstrap; each is added when a feature needs it.

## Open questions

- Criteria for starting the native graph engine (ADR-006).
- Engine-neutral design of checkpoints and interrupts.
- How tool-calling is modelled provider-neutrally (ADR-004).
