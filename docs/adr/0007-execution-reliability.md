# ADR-007: Execution reliability contracts

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

Bootstrap v0.1 had fail-fast nodes, a graph step limit and a node trace. It could not retry
transient failures, bound a cooperative async call, or send an exhausted failure to a recovery
node. These capabilities must work through any future `WorkflowEngine`, without exposing
LangGraph types or adding fields to every workflow state.

## Decision

- A `WorkflowNode` may have a framework-owned `NodeExecutionPolicy`. Without one, it runs once
  with no timeout. A `RetryPolicy` names eligible exception classes and/or explicitly enables
  timeout retries. `max_attempts` includes the first call. Delays use bounded exponential
  backoff and are cancellable.
- Timeout is **per attempt**, implemented with `asyncio.timeout`. It cooperatively cancels the
  async node; it cannot stop blocking synchronous code or undo an external side effect.
- `Workflow.add_error_edge(source, recovery)` attaches a separate failure route. The target
  must be a `RecoveryNode`, whose `recover(state, NodeFailure)` receives structured context.
  The normal edge or conditional route still handles success. Retries exhaust before an error
  edge is taken. Without an error edge, execution remains fail-fast.
- Cancellation propagates. Configuration, Pydantic validation, state-transition and workflow
  validation errors are neither retried nor routed. Framework `WorkflowExecutionError`s keep
  their category and also fail fast, including step limit, routing and invalid state failures.
  Other node exceptions and configured timeouts can enter recovery after retry exhaustion.
- Every attempt receives a deep Pydantic model copy of the last committed state. Failed node
  mutations stay in that attempt; retries and recovery see the committed state. Successful
  outputs are round-trip revalidated before LangGraph receives the update. Wrong-type or
  invalid outputs are `INVALID_STATE` failures with node records and no retry or recovery.
- `WorkflowResult.records` contains one `ExecutionRecord` per node visit, including attempts,
  timeout count, outcome, last error category and recovery target. `trace` keeps its earlier
  node-name behavior. Unhandled execution errors carry the same records and a structured
  category; `NodeExecutionError.failure` holds the final `NodeFailure`.
- Failure messages and records omit raw exception messages, prompts, tool arguments and
  credentials. The original exception remains chained on an unhandled node failure for
  debugging, as in v0.1; callers must treat its traceback as potentially sensitive.

The retry loop and policy types are engine-neutral. The LangGraph adapter implements error
routing with a run-local failure context and conditional edges. A native engine must satisfy
the same tests in `tests/test_execution_reliability.py`.

## Consequences and limits

Retries are **explicit** because a node may perform non-idempotent work. A caller must ensure
that an operation is safe to repeat or use an idempotency key. Timeout or cancellation does not
prove that a remote operation was rolled back. `ToolRegistry.execute` returns `ToolResult` for
tool failures; a failed result does not raise and therefore does not trigger node retry or
recovery unless the node explicitly interprets it and raises an eligible exception. The
registry itself has no automatic retries.

Recovery can return a successful state, an explicit failed agent state, or raise an error. A
routed failure remains visible in `ExecutionRecord` even if recovery succeeds. Deep copying
costs time and memory proportional to state size on every attempt; output revalidation also
adds work on success. Custom mutable field types must implement correct deep-copy behavior,
and custom serializers and validators must support Pydantic round trips. A copy or validation
failure is reported as `INVALID_STATE`. Node authors should still return new states where
practical. External side effects, such as tool calls and remote writes, cannot be rolled back.

This decision adds no persistent checkpoints, event bus, distributed scheduler, or general
rollback mechanism.
