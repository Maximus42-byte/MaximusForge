# ADR-006: LangGraph first, native engine later

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

Building a durable, correct graph execution engine is substantial work. LangGraph provides one
today, but depending on it directly throughout the codebase would make it a permanent
constraint on our API, state model and release cadence.

## Decision

Use LangGraph as the initial execution backend, **confined behind a boundary**:

```text
Agent
  |  builds
Workflow          (engine-neutral definition: nodes, edges, routers, state type)
  |  executed by
WorkflowEngine    (ABC: execute(workflow, initial_state, config) -> WorkflowResult)
  |  implemented by
LangGraphEngine   (maximusforge/workflows/engines/langgraph_engine.py)   <- only LangGraph import
```

Rules, enforced mechanically:

- Only `workflows/engines/langgraph_engine.py` may import `langgraph` (Ruff `TID251` bans it
  elsewhere; `tests/test_architecture.py` verifies it by parsing imports and checks that
  importing the framework does not load LangGraph).
- LangGraph types do not appear in any public signature. Nodes and routers are plain
  MaximusForge types; the adapter wraps them.
- The engine is stateless between calls (the graph is compiled per execution).
- The adapter normalises behaviour to the `WorkflowEngine` contract: node exceptions become
  `NodeExecutionError`, recursion limits become `WorkflowExecutionError`, undeclared routes
  are rejected.

**Where the native engine plugs in:** add a sibling module in `workflows/engines/` that
implements `WorkflowEngine`, then add it to the `engine` fixture in `tests/conftest.py`
so the whole contract suite (`tests/test_workflow_engine.py`) runs against both engines.
Agents are constructed with an engine and need no changes.

## Rationale

Starting on LangGraph delivers a working execution layer immediately, while the interface and
contract tests capture the semantics we actually depend on, which is what makes replacement
a bounded task rather than a rewrite.

## Consequences

- The `WorkflowEngine` interface is intentionally the *intersection* of what we need, not the
  union of LangGraph features. Features LangGraph has but we have not modelled (checkpointers,
  interrupts, streaming, parallel branches) are not reachable until we design them
  engine-neutrally.
- State types must be Pydantic models, since that is what the adapter passes to LangGraph.
- Some LangGraph behaviour (e.g. how state is returned across versions) is absorbed in
  the adapter, which is the single place to fix when LangGraph changes.

## Future considerations

- Design engine-neutral checkpointing and interrupt/resume before using the LangGraph
  equivalents, so the abstraction does not leak.
- Decide the criteria for starting the native engine (e.g. features LangGraph cannot provide,
  or maintenance cost of the adapter exceeding the cost of owning the engine).
- Pin and test against a supported LangGraph version range in CI once CI exists.
