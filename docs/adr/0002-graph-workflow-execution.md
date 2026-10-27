# ADR-002: Graph workflow execution

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

Autonomous agents need control flow that is more than a single prompt loop: planning steps,
tool use, branching on outcomes, retries, failure handling, human approval, and the ability to
resume after a crash. Hard-coding this as ad-hoc loops makes behaviour opaque and hard to test.

## Decision

An agent's behaviour is a **graph workflow**: nodes (units of work) connected by edges, with
conditional routing based on state. A `Workflow` is an engine-neutral definition; a
`WorkflowEngine` executes it (see ADR-006).

Target capabilities, in the order they will be delivered:

| Capability | Status in v0.1 |
|---|---|
| Nodes, edges, entry point | Implemented |
| Conditional routing, loops | Implemented |
| Max-step guard, node-failure wrapping, execution trace | Implemented |
| Retries with backoff | Planned |
| Failure-handling routes (error edges) | Planned |
| Parallel fan-out / join | Planned |
| Human-in-the-loop (interrupt/resume) | Planned |
| Persistent, resumable execution (checkpointing) | Planned |

## Rationale

Graphs make control flow explicit, inspectable and testable, and give a natural place to hang
retries, checkpoints and observability. Because state is passed between nodes as a value,
every transition can be recorded and replayed.

## Consequences

- Authors express agents as nodes plus routing, which is more structured than a free-form
  loop.
- v0.1 intentionally restricts each node to one outgoing connection and sequential
  execution; the definition API will grow additively.
- Node state is treated as immutable (nodes return a new state), which is what makes
  checkpointing and replay tractable later.

## Future considerations

- Define a serialisable state contract before introducing persistence.
- Decide how human-in-the-loop interrupts are represented in the engine interface.
