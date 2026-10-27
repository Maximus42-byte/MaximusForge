# Architecture

This document describes the architecture of MaximusForge **as implemented in Bootstrap v0.1**,
and marks where planned components will attach. Decisions and their rationale live in the
[ADRs](adr/).

## Layers and dependency direction

```text
                         ┌────────────────────────────┐
                         │          agents            │  Agent, AgentState, ResearchAgent
                         └──────────────┬─────────────┘
                 builds Workflow, uses  │
        ┌──────────────┬────────────────┼───────────────┬──────────────┐
        ▼              ▼                ▼               ▼              ▼
   workflows        planning           llm            tools          memory
 (Workflow,        (Planner,       (LLMProvider,   (Tool,         (MemoryStore,
  WorkflowEngine)   Plan)           Message...)     ToolRegistry)  SemanticMemoryStore)
        │              │                │               │              │
        └──────────────┴────────────────┴───────┬───────┴──────────────┘
                                                ▼
                                      core (Task, errors)

 workflows/engines/langgraph_engine.py  ──►  langgraph        (the only such import)
 llm/openai_compatible.py               ──►  httpx            (wire format, no vendor SDK)
```

Rules: dependencies point downward; `core` imports nothing from the framework; `agents` is the
only layer that composes the others; no cycles. Backends (LangGraph, httpx, a future
PostgreSQL driver) are leaf modules reachable only through an interface.

## Execution model

```text
Agent.run(task)
   │  initial = AgentState.for_task(task) → RUNNING
   ▼
Workflow            engine-neutral graph: nodes, edges, conditional edges, entry, state type
   │  engine.execute(workflow, initial, config)
   ▼
WorkflowEngine      ABC — the replaceable boundary (ADR-006)
   │
   ▼
LangGraphEngine     compiles the Workflow to a StateGraph per call, wraps nodes/routers
```

- **Nodes** are `WorkflowNode[S]` with `async run(state) -> state`. Nodes should treat state as
  immutable and return a new state. Pydantic freezing prevents top-level assignment; nested
  dictionaries remain mutable.
- **Routing**: a node has exactly one outgoing connection — an `Edge`, or a `ConditionalEdge`
  whose router maps state to a route key. `END` terminates.
- **Results**: `WorkflowResult(state, trace)`, where `trace` is the ordered node names that
  ran. Failures raise `NodeExecutionError` (original exception chained, trace attached) or
  `WorkflowExecutionError` (step limit, bad route).
- **`Agent.run`** guarantees the returned state is terminal (`COMPLETED` or `FAILED`),
  otherwise raises `AgentError`.

### Agent state machine

```text
PENDING ──► RUNNING ──► COMPLETED
   │           │
   └──► FAILED ◄┘          (COMPLETED and FAILED are terminal)
```

`AgentState` is the working memory of a run: the `Task`, status, `Plan`, a tuple of
`ExecutionStep`s (action, tool, arguments, observation, error), output and error. It records
what was *done*, not model reasoning (ADR-005).

## Components

| Package | Key types | Notes |
|---|---|---|
| `core` | `Task`, `MaximusForgeError` hierarchy | No framework dependencies |
| `workflows` | `Workflow`, `WorkflowNode`, `FunctionNode`, `WorkflowEngine`, `ExecutionConfig`, `WorkflowResult` | Engine-neutral |
| `workflows.engines` | `LangGraphEngine` | Import by module path; only LangGraph importer |
| `planning` | `Plan`, `PlanStep`, `Planner`, `LLMPlanner` | |
| `llm` | `LLMProvider`, `Message`, `LLMResponse`, `OpenAICompatibleProvider`, `ScriptedLLMProvider` (`llm.testing`) | OpenAI-compatible adapter covers hosted and local servers |
| `tools` | `Tool[In, Out]`, `ToolRegistry`, `ToolResult`, `ToolSpec` | Typed Pydantic I/O; failures are results, not exceptions |
| `memory` | `MemoryStore`, `SemanticMemoryStore`, `MemoryRecord`, `MemoryQuery`, `InMemoryStore` | Interfaces + a non-durable reference store |
| `agents` | `Agent`, `AgentState`, `ResearchAgent` | `ResearchAgent` is a minimal architecture check |

### Tools

A `Tool` declares `name`, `description` and a Pydantic `input_model` as class attributes and
implements `async run(input) -> output`. `ToolRegistry.execute(name, arguments)` validates the
arguments, runs the tool, and returns a `ToolResult` with output, error and duration. Because
tool names and arguments usually come from model output, unknown tools, invalid arguments and
tool exceptions are all returned as `ok=False` results. Duplicate registration raises
`DuplicateToolError` unless `replace=True`.

### LLM layer

Agents see `LLMProvider.generate(messages, options=...) -> LLMResponse` and nothing else.
Credentials are supplied via configuration (`OpenAICompatibleSettings`, optionally
`from_env()`); nothing reads the environment at import time.

### Memory

Three tiers (ADR-003): working memory is `AgentState`; durable records are a `MemoryStore`;
similarity retrieval is a `SemanticMemoryStore`. Only `InMemoryStore` exists, and it is for
tests and experiments.

## Extension points

| To add… | Implement… | Wire it in… |
|---|---|---|
| A new agent | Subclass `Agent`, implement `build_workflow()` | Construct with an engine and its dependencies |
| A native graph engine | `WorkflowEngine` in `workflows/engines/` | Add to the `engine` fixture in `tests/conftest.py`; contract tests run against it |
| An LLM provider | `LLMProvider` | Inject into agents/planners |
| A tool | `Tool` subclass | `ToolRegistry.register()` |
| A PostgreSQL/pgvector backend | `SemanticMemoryStore` | Inject where memory is needed |

## Not yet designed (deliberately)

Retries and error edges, parallel branches, human-in-the-loop, checkpointing and resumption,
streaming, native LLM tool-calling and ReAct nodes, embeddings, RAG, MCP, multi-agent
orchestration, evaluation, security tooling, and observability. See the
[roadmap](roadmap.md).

## Architectural tests

`tests/test_architecture.py` enforces: only the adapter imports LangGraph; no vendor LLM SDK
is imported; importing the framework neither loads LangGraph nor needs credentials. Ruff's
`TID251` rule provides the same LangGraph guard at lint time.
