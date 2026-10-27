# MaximusForge

> A framework for graph-based autonomous AI systems.

**Status: pre-alpha (Bootstrap v0.1).** The architectural foundation and a minimal executable
core exist. Most of the platform described below is planned, not built. APIs will change.

## Overview

MaximusForge is a Python framework for agentic AI systems where agent behaviour is an explicit,
inspectable **graph workflow** rather than an opaque prompt loop. The goal is a codebase in
which agents, models, tools, memory and execution engines are independently replaceable,
testable without paid APIs, and measurable, with evaluation and security treated as first-class
concerns rather than afterthoughts.

It is not a tutorial, a chatbot demo, or a thin wrapper around an existing agent framework.
Where it uses one (LangGraph), it does so behind an interface it controls.

## Why it exists

Many agent frameworks make it easy to get a demo running and hard to answer production
questions: What exactly did the agent do? Can I swap the model or the execution engine? Can I
test this without calling an API? Does it leak data? MaximusForge aims to make those questions
answerable by design:

- behaviour is a graph of typed steps with a recorded execution trace;
- every external dependency (LLM, tools, memory, graph engine) sits behind a small interface;
- reasoning is represented as plans, actions, observations and state transitions, never as
  stored model chain-of-thought;
- evaluation and security tooling are part of the long-term scope.

## Design principles

1. **Interfaces at every boundary.** Agents depend on abstractions, not vendors.
2. **No lock-in.** Not to an LLM provider, and not to LangGraph ([ADR-006](docs/adr/0006-langgraph-first-native-engine-later.md)).
3. **Explicit over implicit.** Constructor injection, no global mutable state, no import-time side effects.
4. **Operational reasoning.** Plans, tool calls, observations and traces are the record; hidden reasoning is not exposed or stored.
5. **Testable offline.** The suite runs with no network, API keys, or database.
6. **Earn each abstraction.** Nothing is added speculatively; unfinished work is labelled as such.

## Architecture at a glance

```text
Agent
  │ builds
Workflow  ── nodes, edges, conditional routing (engine-neutral)
  │ executed by
WorkflowEngine  (interface)
  │
LangGraphEngine  (adapter; the only module that imports LangGraph)
```

Alongside the workflow, agents use injected `LLMProvider`, `ToolRegistry`, `Planner` and
`MemoryStore` interfaces. See [docs/architecture.md](docs/architecture.md) for the layers,
dependency rules and extension points, and [docs/adr/](docs/adr/) for the decisions behind them.

## Project status

### Available now

- Workflow definition API (nodes, edges, conditional edges/loops, validation) and the
  `WorkflowEngine` interface
- LangGraph-backed engine behind that interface, with a step limit, node-failure wrapping and
  an execution trace
- `Agent` base class, frozen `AgentState` with enforced status transitions
- Provider-independent `LLMProvider`, an OpenAI-compatible reference adapter (hosted or local
  servers), and a scripted fake for tests
- `Tool` / `ToolRegistry` with Pydantic-validated inputs and structured results
- `Planner` / `LLMPlanner`
- `MemoryStore` / `SemanticMemoryStore` interfaces and a **non-durable** `InMemoryStore`
  (for tests and experiments only)
- A minimal `ResearchAgent` that exercises the pieces above end to end (a skeleton to validate
  the architecture, not the future Research Assistant)

### In development

Nothing is actively in progress beyond Bootstrap v0.1. Next up is roadmap Phase 1: retries,
error routing, execution events, native tool-calling and a bounded ReAct node.

### Planned

PostgreSQL + pgvector memory · RAG pipelines · checkpointed/resumable execution ·
human-in-the-loop · MCP integration · multi-agent orchestration · additional LLM providers ·
evaluation and benchmarking · observability · AI security and privacy evaluation · a native
MaximusForge graph engine · the flagship agents (Research Assistant, Code Review, AI Security,
Membership Inference Audit, Agent Evaluation Platform) · deployment infrastructure.

Nothing in the "Planned" list exists in this repository yet. See the [roadmap](docs/roadmap.md).

## Technology direction

Python 3.11+ · Pydantic v2 · LangGraph (initial graph backend, isolated) · httpx · pytest ·
Ruff · mypy (strict). PostgreSQL + pgvector is the intended persistent memory backend
([ADR-003](docs/adr/0003-postgresql-pgvector-memory.md)).

## Repository structure

```text
MaximusForge/
├── src/maximusforge/
│   ├── core/            Task, shared error hierarchy
│   ├── workflows/       Workflow definition, WorkflowEngine interface
│   │   └── engines/     LangGraph adapter (only place LangGraph is imported)
│   ├── agents/          Agent base class, AgentState, ResearchAgent
│   ├── planning/        Plan, Planner, LLMPlanner
│   ├── llm/             LLMProvider, types, OpenAI-compatible adapter, test fake
│   ├── tools/           Tool, ToolRegistry
│   └── memory/          MemoryStore interfaces, InMemoryStore
├── tests/               Unit and architecture tests (no network required)
├── examples/            Offline runnable example
├── docs/                architecture.md, roadmap.md, adr/
├── pyproject.toml
└── .env.example         Variable names only
```

Packages for RAG, evaluation, security and observability will be created when their first real
code lands.

## Getting started

Requires Python 3.11 or newer.

```bash
python3.11 -m venv .venv && source .venv/bin/activate   # or any 3.11+ interpreter
pip install -e ".[dev]"

pytest                       # tests (no API keys needed)
ruff check .                 # lint
mypy                         # type check

python examples/research_agent_offline.py
```

The example uses a scripted LLM and a canned search tool, so it runs fully offline. To use a
real model, copy `.env.example` to `.env`, set the `MAXIMUSFORGE_LLM_*` variables, and build the
provider with `OpenAICompatibleProvider(OpenAICompatibleSettings.from_env())`. Servers
exposing OpenAI-compatible chat completions can work, including Ollama and vLLM. Credentials
are never read unless you ask for them.

## Development philosophy

Small cohesive modules, composition over inheritance, interfaces before implementations, and
documentation of decisions as ADRs. Architectural rules are enforced by tests and lint rather
than by convention. Features are described as available only when they are implemented and
tested.

## Contributing

The project is at an early stage and the architecture is still settling. Before opening a
substantial pull request, please open an issue to discuss it, and read the relevant ADRs.
Contributions are expected to include tests, pass `ruff` and `mypy`, and respect the dependency
rules in [docs/architecture.md](docs/architecture.md). A fuller contributing guide will follow.

## License

No license has been chosen yet. Until one is added, all rights are reserved by the author.
