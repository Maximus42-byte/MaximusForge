# ADR-005: Hybrid reasoning — planner + graph workflow + ReAct-capable nodes

- **Status:** Accepted (planner and graph implemented; ReAct-capable nodes planned)
- **Date:** 2026-10-27

## Context

Pure ReAct loops are flexible but hard to bound, test and audit. Pure static workflows are
predictable but cannot adapt. We want predictability at the macro level and flexibility where
it pays off.

## Decision

Combine three layers:

1. **Planner** (agent level): turns a task into an explicit `Plan` of steps (`Planner`,
   `LLMPlanner`).
2. **Graph workflow** (control flow): nodes, edges and routing decide what runs next and when
   to stop (ADR-002).
3. **ReAct-capable nodes** (step level): a node may run a bounded tool-using loop internally
   when a step needs it.

**Reasoning is represented operationally**: plans, actions, tool calls, observations, state
transitions and execution metadata (`Plan`, `ExecutionStep`, `AgentState`, `ToolResult`,
workflow traces). The framework does not expose, request, or store a model's hidden
chain-of-thought, and no API is designed around doing so. `LLMPlanner` keeps only the parsed
steps and discards any other model text.

## Rationale

Explicit plans and traces are what make agents debuggable, evaluable and auditable. They are
also the data that evaluation and security tooling will consume. Bounding ReAct inside a node
keeps open-ended behaviour contained by the surrounding graph (step limits, retries, approval).

## Consequences

- Observability and evaluation attach to structured records rather than free-form text.
- v0.1 has a planner and a graph but no ReAct node: that needs native tool-calling in the LLM
  layer (ADR-004), which is not yet modelled.

## Future considerations

- A `ReActNode` with a hard iteration cap and per-step tool allow-list.
- Re-planning (a conditional edge from execution back to planning on failure).
- Plan validation (e.g. checking steps against registered tools) before execution.
