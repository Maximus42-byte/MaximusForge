# ADR-008: Structured execution events

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

Execution reliability records summarize each completed node visit, but observers cannot see
attempt starts, retry scheduling, recovery routing, or cancellation while a workflow runs.
Events must retain the `WorkflowEngine` boundary and must not alter retry, timeout, or recovery
behavior.

## Decision

A workflow run may receive an `EventSink` through `ExecutionConfig(event_sink=...)`. With no
sink, execution needs no logging configuration or external service. The public event and sink
types live in `maximusforge.events`; they contain no LangGraph types. Each call creates a UUID
run ID, available on emitted events and on a successful `WorkflowResult.run_id`. A sequence
number starts at 1 for each run, giving a deterministic order even when runs share a sink.
`run_id` does not participate in `WorkflowResult` equality, preserving prior comparisons.
Timestamps use a UTC offset of zero, including values supplied to the public
`ExecutionEvent` constructor; the engine generates them with `datetime.now(UTC)`.
`ExecutionEvent` is frozen and contains only immutable metadata: event type,
run/workflow identity, optional node and attempt, failure category,
duration, next attempt and delay, and recovery source or target. It carries no state, exception
message, prompt, tool arguments, credentials, or model reasoning.

The event taxonomy is:

| Event | Meaning |
|---|---|
| `WORKFLOW_STARTED` | Execution entered the engine, before workflow validation. |
| `NODE_STARTED` | One node attempt is about to begin, including recovery attempts. |
| `NODE_ATTEMPT_FAILED` | An attempt failed or timed out; its category and duration are final. |
| `NODE_RETRY_SCHEDULED` | Retry eligibility is confirmed; backoff begins next. |
| `NODE_COMPLETED` | A node visit completed successfully after output validation and records. |
| `NODE_FAILED` | A node visit exhausted or failed fast; recovery may still follow. |
| `NODE_RECOVERY_ROUTED` | The failure edge was actually selected. |
| `WORKFLOW_COMPLETED` | A successful result was constructed. |
| `WORKFLOW_FAILED` | Execution selected a terminal failure. |
| `WORKFLOW_CANCELLED` | Cancellation propagated before another terminal event was selected. Best effort delivery. |

Recovery uses the same node events as ordinary work. Those events set `recovery_for` to the
source node, and the route event names the recovery target. A successful recovery therefore
ends with `WORKFLOW_COMPLETED`; a failed recovery ends with `WORKFLOW_FAILED`. Node failure
alone does not imply terminal workflow failure.

## Ordering and authoritative metadata

For a sequential run, events are awaited in transition order. A normal one-node execution is
`WORKFLOW_STARTED`, `NODE_STARTED(1)`, `NODE_COMPLETED(1)`, `WORKFLOW_COMPLETED`. A retry inserts
`NODE_ATTEMPT_FAILED(n)`, `NODE_RETRY_SCHEDULED(n, n+1)`, then `NODE_STARTED(n+1)`. Exhaustion
emits `NODE_ATTEMPT_FAILED`, `NODE_FAILED`, and either `NODE_RECOVERY_ROUTED` or
`WORKFLOW_FAILED`. `NODE_RECOVERY_ROUTED` precedes the recovery node's `NODE_STARTED`.
Cancellation emits no ordinary failure or retry event for the interrupted attempt and never
becomes recovery routing.

The engine-neutral retry loop reports attempt starts, classified failures, and retry decisions
to a private observer. The LangGraph adapter translates these transitions to public events.
It does not maintain another retry counter or error classifier. `ExecutionRecord` remains the
authoritative summary of a completed node visit, including total attempts and timeouts; events
show the transitions while that visit is in progress. Event categories and attempt numbers
come from the same reliability calculations as records.

## Terminal lifecycle

The emitter selects a terminal transition when it constructs `WORKFLOW_COMPLETED`,
`WORKFLOW_FAILED`, or `WORKFLOW_CANCELLED`, immediately before awaiting the sink. Selection
is final for that run: no later event or conflicting terminal transition is attempted.
Completion is selected after the result is constructed; failure is selected in the error
handler. Cancellation selects `WORKFLOW_CANCELLED` only if neither transition was already
selected. If the caller cancels while a sink is delivering a selected completion or failure,
cancellation still propagates to the caller, but the selected terminal event remains the only
terminal attempt. If cancellation interrupts an ordinary event such as `NODE_STARTED`, the
engine attempts `WORKFLOW_CANCELLED` and does not retry, recover, or continue to another node.

Terminal selection is local to the execution task. It does not make delivery and receipt atomic:
an asynchronous sink can receive an event and suspend before returning, or be cancelled before
receiving it. The emitter attempts at most one terminal event per run, but does not promise
exactly-once delivery or a terminal event for every observer.

## Delivery policy

Delivery is inline and awaited in the executing task. No detached tasks, global registry,
queue, or persistence are introduced. An `EventSink.emit` exception other than cancellation is
caught, and only its event type and exception type are logged through the module logger;
execution continues. Logging failures are also ignored so diagnostics cannot change results.
A sink's failure cannot turn successful workflow logic into a workflow failure. External
`CancelledError` propagates. Slow sinks can delay execution, so sinks should be lightweight;
event delivery occurs outside the per-attempt timeout interval.

When cancellation selects `WORKFLOW_CANCELLED`, its delivery is attempted with a
zero-timeout await. An in-memory sink can receive it without suspending. A sink that
would block is stopped at its first await, and the original cancellation is re-raised.
Thus cancellation diagnostics are best effort and cannot hold the workflow open. The optional `LoggingEventSink` writes a safe
metadata dictionary to a caller-supplied Python logger without changing global logging.
`InMemoryEventCollector` provides a small offline reference sink. A shared collector can
receive interleaved runs; consumers group by run ID and order by sequence.

## Consequences and limits

Events are transient observations, not checkpoints or a durable audit log. A failing sink can
lose events, and a cancelled run may lose its final cancellation event. Durations use a
monotonic clock and include inline sink time for node-visit and workflow totals; attempt failure
durations exclude delivery time. Events do not roll back external side effects. This milestone
adds no OpenTelemetry, metrics, broker, database, or evaluation subsystem. Future observability
and evaluation consumers can implement `EventSink` without changing workflow state or binding
their API to LangGraph. A sink that drops events may leave gaps in the delivered sequence.
