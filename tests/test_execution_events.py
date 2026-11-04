"""Backend-neutral execution event contract tests."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from maximusforge.core import (
    FailureCategory,
    NodeExecutionError,
    NodeFailure,
    WorkflowExecutionError,
    WorkflowValidationError,
)
from maximusforge.events import (
    EventSink,
    ExecutionEvent,
    InMemoryEventCollector,
    LoggingEventSink,
)
from maximusforge.events import (
    ExecutionEventType as Kind,
)
from maximusforge.workflows import (
    END,
    ExecutionConfig,
    FunctionNode,
    FunctionRecoveryNode,
    NodeExecutionPolicy,
    RetryPolicy,
    Workflow,
    WorkflowEngine,
    reliability,
)


class State(BaseModel):
    value: int = 0


class TransientError(Exception):
    pass


def _workflow(run: Any, policy: NodeExecutionPolicy | None = None) -> Workflow[State]:
    return (
        Workflow("events", State)
        .add_node(FunctionNode("work", run, policy=policy))
        .set_entry("work")
        .add_edge("work", END)
    )


def _with_recovery(
    run: Any, recover: Any, policy: NodeExecutionPolicy | None = None
) -> Workflow[State]:
    return (
        Workflow("events", State)
        .add_node(FunctionNode("work", run, policy=policy))
        .add_node(FunctionRecoveryNode("fix", recover))
        .set_entry("work")
        .add_edge("work", END)
        .add_error_edge("work", "fix")
        .add_edge("fix", END)
    )


async def _pass(state: State) -> State:
    return state.model_copy(update={"value": state.value + 1})


def _kinds(collector: InMemoryEventCollector) -> list[Kind]:
    return [event.type for event in collector.events]


def test_event_model_is_typed_timestamped_and_frozen() -> None:
    before = datetime.now(UTC)
    event = ExecutionEvent(Kind.WORKFLOW_STARTED, uuid4(), "demo", 1)
    after = datetime.now(UTC)
    assert before <= event.timestamp <= after
    assert event.timestamp.tzinfo is UTC
    offset = event.timestamp.utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    with pytest.raises(FrozenInstanceError):
        cast(Any, event).sequence = 2


def test_event_rejects_non_utc_timestamp() -> None:
    with pytest.raises(ValueError, match="UTC"):
        ExecutionEvent(
            Kind.WORKFLOW_STARTED,
            uuid4(),
            "demo",
            1,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=3))),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "workflow_started"},
        {"run_id": "not-a-uuid"},
        {"workflow_name": None},
        {"sequence": 0},
        {"timestamp": datetime(2026, 1, 1)},
        {"node_name": "work"},
        {"type": Kind.NODE_STARTED, "node_name": "work"},
        {"type": Kind.NODE_STARTED, "node_name": "work", "attempt": 0},
        {"type": Kind.NODE_ATTEMPT_FAILED, "node_name": "work", "attempt": 1},
        {"type": Kind.WORKFLOW_FAILED, "category": FailureCategory.NODE_ERROR},
        {"type": Kind.WORKFLOW_COMPLETED, "duration_seconds": -1.0},
        {"type": Kind.NODE_RECOVERY_ROUTED, "node_name": "work", "attempt": 1,
         "category": FailureCategory.NODE_ERROR},
    ],
)
def test_invalid_event_metadata_is_rejected(changes: dict[str, Any]) -> None:
    kwargs: dict[str, Any] = {
        "type": Kind.WORKFLOW_STARTED,
        "run_id": uuid4(),
        "workflow_name": "demo",
        "sequence": 1,
    }
    kwargs.update(changes)
    with pytest.raises(ValueError):
        ExecutionEvent(**kwargs)


async def test_normal_order_and_shared_run_id(engine: WorkflowEngine) -> None:
    collector = InMemoryEventCollector()
    result = await engine.execute(
        _workflow(_pass), State(), config=ExecutionConfig(event_sink=collector)
    )
    assert result.state.value == 1
    assert _kinds(collector) == [
        Kind.WORKFLOW_STARTED, Kind.NODE_STARTED, Kind.NODE_COMPLETED, Kind.WORKFLOW_COMPLETED
    ]
    assert isinstance(result.run_id, UUID)
    assert {event.run_id for event in collector.events} == {result.run_id}
    assert [event.sequence for event in collector.events] == [1, 2, 3, 4]
    assert all(event.workflow_name == "events" for event in collector.events)
    assert collector.events[1].node_name == "work"
    assert collector.events[1].attempt == 1
    assert collector.events[2].duration_seconds is not None
    assert collector.events[3].duration_seconds is not None
    assert result.records[0].attempts == collector.events[2].attempt


async def test_existing_empty_workflow_name_remains_valid(engine: WorkflowEngine) -> None:
    collector = InMemoryEventCollector()
    workflow = (
        Workflow("", State)
        .add_node(FunctionNode("work", _pass))
        .set_entry("work")
        .add_edge("work", END)
    )
    result = await engine.execute(workflow, State(), config=ExecutionConfig(event_sink=collector))
    assert result.state.value == 1
    assert all(event.workflow_name == "" for event in collector.events)


async def test_no_sink_keeps_normal_workflow_behavior(engine: WorkflowEngine) -> None:
    first = await engine.execute(_workflow(_pass), State())
    second = await engine.execute(_workflow(_pass), State())
    assert first.state.value == 1
    assert isinstance(first.run_id, UUID)
    assert first.run_id != second.run_id
    assert first == second  # Run identity does not alter existing WorkflowResult equality.
    assert first.records[0].attempts == 1


async def test_first_attempt_success_has_no_retry_events(engine: WorkflowEngine) -> None:
    collector = InMemoryEventCollector()
    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=(TransientError,), initial_delay=0)
    )
    await engine.execute(
        _workflow(_pass, policy), State(), config=ExecutionConfig(event_sink=collector)
    )
    assert _kinds(collector) == [
        Kind.WORKFLOW_STARTED, Kind.NODE_STARTED, Kind.NODE_COMPLETED, Kind.WORKFLOW_COMPLETED
    ]


async def test_retry_success_uses_authoritative_attempts_and_categories(
    engine: WorkflowEngine,
) -> None:
    collector = InMemoryEventCollector()
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientError("private detail")
        return await _pass(state)

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=2, retry_on=(TransientError,), initial_delay=0)
    )
    result = await engine.execute(
        _workflow(run, policy), State(), config=ExecutionConfig(event_sink=collector)
    )
    assert _kinds(collector) == [
        Kind.WORKFLOW_STARTED,
        Kind.NODE_STARTED,
        Kind.NODE_ATTEMPT_FAILED,
        Kind.NODE_RETRY_SCHEDULED,
        Kind.NODE_STARTED,
        Kind.NODE_COMPLETED,
        Kind.WORKFLOW_COMPLETED,
    ]
    assert [event.attempt for event in collector.events[1:6]] == [1, 1, 1, 2, 2]
    assert collector.events[2].category is FailureCategory.NODE_ERROR
    assert collector.events[3].category is result.records[0].last_error_category
    assert collector.events[3].next_attempt == 2
    assert collector.events[3].retry_delay_seconds == 0
    assert result.records[0].attempts == 2
    assert "private detail" not in repr(collector.events)


async def test_retry_exhaustion_emits_node_and_workflow_failures(engine: WorkflowEngine) -> None:
    collector = InMemoryEventCollector()

    async def fail(state: State) -> State:
        raise TransientError("secret")

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=2, retry_on=(TransientError,), initial_delay=0)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(
            _workflow(fail, policy), State(), config=ExecutionConfig(event_sink=collector)
        )
    assert _kinds(collector) == [
        Kind.WORKFLOW_STARTED, Kind.NODE_STARTED, Kind.NODE_ATTEMPT_FAILED,
        Kind.NODE_RETRY_SCHEDULED, Kind.NODE_STARTED, Kind.NODE_ATTEMPT_FAILED,
        Kind.NODE_FAILED, Kind.WORKFLOW_FAILED,
    ]
    assert collector.events[-2].attempt == info.value.records[0].attempts == 2
    assert collector.events[-2].category is info.value.category
    assert collector.events[-1].category is info.value.category
    assert Kind.NODE_COMPLETED not in _kinds(collector)


@pytest.mark.parametrize("retry_timeouts", [False, True])
async def test_timeout_events_match_retry_policy(
    engine: WorkflowEngine, retry_timeouts: bool
) -> None:
    collector = InMemoryEventCollector()
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
        return state

    retry = (
        RetryPolicy(max_attempts=2, retry_timeouts=True, initial_delay=0)
        if retry_timeouts else None
    )
    policy = NodeExecutionPolicy(timeout_seconds=0.01, retry_policy=retry)
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(
            _workflow(run, policy), State(), config=ExecutionConfig(event_sink=collector)
        )
    assert calls == (2 if retry_timeouts else 1)
    assert info.value.category is FailureCategory.TIMEOUT
    failed_attempts = [
        event for event in collector.events if event.type is Kind.NODE_ATTEMPT_FAILED
    ]
    assert len(failed_attempts) == calls
    assert [event.attempt for event in failed_attempts] == list(range(1, calls + 1))
    assert all(event.category is FailureCategory.TIMEOUT for event in failed_attempts)
    assert sum(event.type is Kind.NODE_RETRY_SCHEDULED for event in collector.events) == calls - 1
    assert Kind.NODE_COMPLETED not in _kinds(collector)
    assert collector.events[-1].type is Kind.WORKFLOW_FAILED


async def test_recovery_route_and_completion_are_distinct(engine: WorkflowEngine) -> None:
    collector = InMemoryEventCollector()

    async def fail(state: State) -> State:
        raise TransientError("route")

    async def recover(state: State, failure: NodeFailure) -> State:
        return state.model_copy(update={"value": failure.attempts})

    result = await engine.execute(
        _with_recovery(fail, recover), State(), config=ExecutionConfig(event_sink=collector)
    )
    assert _kinds(collector) == [
        Kind.WORKFLOW_STARTED, Kind.NODE_STARTED, Kind.NODE_ATTEMPT_FAILED,
        Kind.NODE_FAILED, Kind.NODE_RECOVERY_ROUTED, Kind.NODE_STARTED,
        Kind.NODE_COMPLETED, Kind.WORKFLOW_COMPLETED,
    ]
    route = collector.events[4]
    assert route.node_name == "work"
    assert route.recovery_target == "fix"
    assert result.records[0].failure is not None
    assert route.category is result.records[0].failure.category
    assert collector.events[5].recovery_for == "work"
    assert collector.events[6].recovery_for == "work"
    assert result.state.value == 1
    assert Kind.WORKFLOW_FAILED not in _kinds(collector)


async def test_recovery_failure_is_terminal(engine: WorkflowEngine) -> None:
    collector = InMemoryEventCollector()

    async def fail(state: State) -> State:
        raise TransientError("route")

    async def recover(state: State, failure: NodeFailure) -> State:
        raise RuntimeError("recovery failed")

    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(
            _with_recovery(fail, recover), State(), config=ExecutionConfig(event_sink=collector)
        )
    assert info.value.node == "fix"
    assert collector.events[-3].type is Kind.NODE_ATTEMPT_FAILED
    assert collector.events[-2].type is Kind.NODE_FAILED
    assert collector.events[-2].recovery_for == "work"
    assert collector.events[-1].type is Kind.WORKFLOW_FAILED
    assert Kind.WORKFLOW_COMPLETED not in _kinds(collector)


async def test_preflight_and_routing_failures_are_workflow_events(engine: WorkflowEngine) -> None:
    preflight = InMemoryEventCollector()
    with pytest.raises(WorkflowValidationError):
        await engine.execute(
            Workflow("empty", State), State(), config=ExecutionConfig(event_sink=preflight)
        )
    assert _kinds(preflight) == [Kind.WORKFLOW_STARTED, Kind.WORKFLOW_FAILED]
    assert preflight.events[-1].category is FailureCategory.VALIDATION

    route_events = InMemoryEventCollector()
    workflow = (
        Workflow("route", State)
        .add_node(FunctionNode("work", _pass))
        .set_entry("work")
        .add_conditional_edges("work", lambda state: "bad", {"good": END})
    )
    with pytest.raises(WorkflowExecutionError):
        await engine.execute(
            workflow, State(), config=ExecutionConfig(event_sink=route_events)
        )
    assert route_events.events[-1].type is Kind.WORKFLOW_FAILED
    assert route_events.events[-1].category is FailureCategory.ROUTING
    assert Kind.NODE_FAILED not in _kinds(route_events)


async def test_cancellation_emits_best_effort_terminal_event(engine: WorkflowEngine) -> None:
    collector = InMemoryEventCollector()
    started = asyncio.Event()
    calls = 0
    recoveries = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.Event().wait()
        return state

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=(TransientError,), initial_delay=0)
    )
    task = asyncio.create_task(engine.execute(
        _with_recovery(run, recover, policy), State(),
        config=ExecutionConfig(event_sink=collector),
    ))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1
    assert recoveries == 0
    assert _kinds(collector) == [
        Kind.WORKFLOW_STARTED, Kind.NODE_STARTED, Kind.WORKFLOW_CANCELLED
    ]


async def test_cancellation_during_backoff_ends_pending_wait(
    engine: WorkflowEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = InMemoryEventCollector()
    backoff_started = asyncio.Event()
    backoff_stopped = asyncio.Event()
    calls = 0
    recoveries = 0

    async def controlled_backoff(delay: float) -> None:
        backoff_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            backoff_stopped.set()

    monkeypatch.setattr(reliability, "_sleep_backoff", controlled_backoff)

    async def fail(state: State) -> State:
        nonlocal calls
        calls += 1
        raise TransientError("retry")

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=(TransientError,), initial_delay=1)
    )
    task = asyncio.create_task(engine.execute(
        _with_recovery(fail, recover, policy), State(),
        config=ExecutionConfig(event_sink=collector),
    ))
    await backoff_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backoff_stopped.is_set()
    assert calls == 1
    assert recoveries == 0
    assert _kinds(collector)[-2:] == [Kind.NODE_RETRY_SCHEDULED, Kind.WORKFLOW_CANCELLED]


async def test_failing_sink_cannot_change_success_or_leak_exception(
    engine: WorkflowEngine, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingSink(EventSink):
        async def emit(self, event: ExecutionEvent) -> None:
            raise RuntimeError("secret sink payload")

    caplog.set_level(logging.WARNING, logger="maximusforge.events.sink")
    result = await engine.execute(
        _workflow(_pass), State(), config=ExecutionConfig(event_sink=FailingSink())
    )
    assert result.state.value == 1
    assert len(caplog.records) == 4
    assert "secret sink payload" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_slow_cancellation_sink_cannot_delay_cancellation(engine: WorkflowEngine) -> None:
    started = asyncio.Event()
    cancellation_delivery_started = asyncio.Event()
    cancellation_delivery_stopped = asyncio.Event()

    class SlowCancellationSink(EventSink):
        async def emit(self, event: ExecutionEvent) -> None:
            if event.type is Kind.WORKFLOW_CANCELLED:
                cancellation_delivery_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancellation_delivery_stopped.set()

    async def run(state: State) -> State:
        started.set()
        await asyncio.Event().wait()
        return state

    task = asyncio.create_task(engine.execute(
        _workflow(run), State(), config=ExecutionConfig(event_sink=SlowCancellationSink())
    ))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancellation_delivery_started.is_set()
    assert cancellation_delivery_stopped.is_set()


@pytest.mark.parametrize(
    "terminal_type", [Kind.WORKFLOW_COMPLETED, Kind.WORKFLOW_FAILED]
)
async def test_cancellation_during_terminal_delivery_keeps_first_terminal(
    engine: WorkflowEngine, terminal_type: Kind
) -> None:
    observed = asyncio.Event()
    release = asyncio.Event()
    delivery_stopped = asyncio.Event()
    events: list[Kind] = []

    class BlockingTerminalSink(EventSink):
        async def emit(self, event: ExecutionEvent) -> None:
            events.append(event.type)
            if event.type is terminal_type:
                observed.set()
                try:
                    await release.wait()
                finally:
                    delivery_stopped.set()

    async def run(state: State) -> State:
        if terminal_type is Kind.WORKFLOW_FAILED:
            raise TransientError("node failed")
        return state

    task = asyncio.create_task(engine.execute(
        _workflow(run), State(),
        config=ExecutionConfig(event_sink=BlockingTerminalSink()),
    ))
    await observed.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert delivery_stopped.is_set()
    assert [kind for kind in events if kind in {
        Kind.WORKFLOW_COMPLETED, Kind.WORKFLOW_FAILED, Kind.WORKFLOW_CANCELLED
    }] == [terminal_type]


async def test_cancellation_during_node_started_delivery_stops_execution(
    engine: WorkflowEngine,
) -> None:
    observed = asyncio.Event()
    release = asyncio.Event()
    delivery_stopped = asyncio.Event()
    events: list[Kind] = []
    calls = 0
    recoveries = 0
    later_calls = 0

    class BlockingStartSink(EventSink):
        async def emit(self, event: ExecutionEvent) -> None:
            events.append(event.type)
            if event.type is Kind.NODE_STARTED:
                observed.set()
                try:
                    await release.wait()
                finally:
                    delivery_stopped.set()

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        raise TransientError("retry")

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    async def later(state: State) -> State:
        nonlocal later_calls
        later_calls += 1
        return state

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=2, retry_on=(TransientError,))
    )
    workflow = (
        Workflow("events", State)
        .add_node(FunctionNode("work", run, policy=policy))
        .add_node(FunctionRecoveryNode("fix", recover))
        .add_node(FunctionNode("later", later))
        .set_entry("work")
        .add_edge("work", "later")
        .add_error_edge("work", "fix")
        .add_edge("fix", "later")
        .add_edge("later", END)
    )
    task = asyncio.create_task(engine.execute(
        workflow, State(), config=ExecutionConfig(event_sink=BlockingStartSink())
    ))
    await observed.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert delivery_stopped.is_set()
    assert calls == recoveries == later_calls == 0
    assert events == [
        Kind.WORKFLOW_STARTED, Kind.NODE_STARTED, Kind.WORKFLOW_CANCELLED
    ]


async def test_concurrent_runs_keep_identifiers_and_sequences_separate(
    engine: WorkflowEngine,
) -> None:
    collector = InMemoryEventCollector()
    both_started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        if calls == 2:
            both_started.set()
        await release.wait()
        return state

    workflow = _workflow(run)
    config = ExecutionConfig(event_sink=collector)
    first = asyncio.create_task(engine.execute(workflow, State(value=1), config=config))
    second = asyncio.create_task(engine.execute(workflow, State(value=2), config=config))
    await both_started.wait()
    release.set()
    results = await asyncio.gather(first, second)
    assert results[0].run_id != results[1].run_id
    assert {event.run_id for event in collector.events} == {result.run_id for result in results}
    for result in results:
        events = [event for event in collector.events if event.run_id == result.run_id]
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        assert [event.type for event in events] == [
            Kind.WORKFLOW_STARTED, Kind.NODE_STARTED, Kind.NODE_COMPLETED,
            Kind.WORKFLOW_COMPLETED,
        ]


async def test_logging_sink_uses_structured_safe_metadata(
    engine: WorkflowEngine, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("maximusforge.events.test")
    caplog.set_level(logging.INFO, logger=logger.name)
    sink = LoggingEventSink(logger)
    result = await engine.execute(
        _workflow(_pass), State(), config=ExecutionConfig(event_sink=sink)
    )
    assert len(caplog.records) == 4
    metadata = caplog.records[1].__dict__["maximusforge_event"]
    assert metadata["type"] == Kind.NODE_STARTED.value
    assert metadata["run_id"] == str(result.run_id)
    assert metadata["node_name"] == "work"
    assert "value" not in metadata


def test_invalid_sink_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="event_sink"):
        ExecutionConfig(event_sink=cast(EventSink, "invalid"))


async def test_invalid_state_events_keep_framework_category_and_fail_fast(
    engine: WorkflowEngine,
) -> None:
    collector = InMemoryEventCollector()
    recoveries = 0

    async def invalid(state: State) -> State:
        return state.model_copy(update={"value": "invalid"})

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(
            max_attempts=2, retry_on=(WorkflowExecutionError,), initial_delay=0
        )
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(
            _with_recovery(invalid, recover, policy), State(),
            config=ExecutionConfig(event_sink=collector),
        )
    assert recoveries == 0
    assert _kinds(collector) == [
        Kind.WORKFLOW_STARTED, Kind.NODE_STARTED, Kind.NODE_ATTEMPT_FAILED,
        Kind.NODE_FAILED, Kind.WORKFLOW_FAILED,
    ]
    assert all(
        event.category is FailureCategory.INVALID_STATE
        for event in collector.events if event.category is not None
    )
    assert info.value.records[0].attempts == 1


async def test_step_limit_failure_event_keeps_category(engine: WorkflowEngine) -> None:
    collector = InMemoryEventCollector()
    workflow = (
        Workflow("loop", State)
        .add_node(FunctionNode("work", _pass))
        .set_entry("work")
        .add_conditional_edges("work", lambda state: "again", {"again": "work"})
    )
    with pytest.raises(WorkflowExecutionError) as info:
        await engine.execute(
            workflow, State(), config=ExecutionConfig(max_steps=2, event_sink=collector)
        )
    assert info.value.category is FailureCategory.STEP_LIMIT
    assert collector.events[-1].type is Kind.WORKFLOW_FAILED
    assert collector.events[-1].category is FailureCategory.STEP_LIMIT
    assert sum(event.type is Kind.NODE_COMPLETED for event in collector.events) == 2


async def test_logging_failure_does_not_mask_sink_failure_or_workflow_success(
    engine: WorkflowEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maximusforge.events import sink as sink_module

    class FailingSink(EventSink):
        async def emit(self, event: ExecutionEvent) -> None:
            raise RuntimeError("sink failed")

    def broken_warning(*args: object, **kwargs: object) -> None:
        raise RuntimeError("logger failed")

    monkeypatch.setattr(sink_module._logger, "warning", broken_warning)
    result = await engine.execute(
        _workflow(_pass), State(), config=ExecutionConfig(event_sink=FailingSink())
    )
    assert result.state.value == 1
