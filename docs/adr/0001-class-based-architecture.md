# ADR-001: Class-based architecture

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

MaximusForge will grow to include agents, workflows, tools, memory, providers, evaluation and
security components developed by different contributors over a long period. We need a code
organisation that keeps those parts independently replaceable and testable.

## Decision

Model the framework's extension points as small classes with explicit interfaces:

- Abstract base classes (`abc.ABC`) define the contracts other code depends on
  (`Agent`, `WorkflowNode`, `WorkflowEngine`, `LLMProvider`, `Tool`, `MemoryStore`, `Planner`).
- Collaborators are passed in through constructors (dependency injection). There is no global
  mutable state; for example, a `ToolRegistry` is an instance, not a module-level dict.
- Pydantic models are used where runtime validation earns its cost (task input, tool input,
  state that crosses a graph backend). Frozen dataclasses are used for simple internal
  value objects (LLM messages and responses).
- Interfaces stay small; composition is preferred over inheritance beyond one level.

## Rationale

ABCs make the contract explicit and fail loudly at instantiation if a method is missing.
Constructor injection makes every dependency visible and lets tests substitute fakes without
patching. Keeping classes small avoids monolithic "god agents".

## Consequences

- Slightly more ceremony than plain functions; accepted in exchange for clear seams.
- Every new abstraction must justify itself with a current or near-term consumer.
- Behaviour is exercised in tests through fakes of the interfaces, not mocks of internals.

## Future considerations

- `typing.Protocol` may replace an ABC where structural typing is more convenient for
  third-party implementers.
- If a hot path shows measurable overhead from class dispatch, revisit locally rather than
  globally.
