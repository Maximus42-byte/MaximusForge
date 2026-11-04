"""Reliability contract tests; each engine implementation should pass these unchanged."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from pydantic import BaseModel, Field, ValidationError

from maximusforge.core import (
    ConfigurationError,
    ExecutionOutcome,
    FailureCategory,
    NodeExecutionError,
    NodeFailure,
    WorkflowExecutionError,
)
from maximusforge.tools import ToolRegistry
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

from .conftest import FakeSearchTool


class State(BaseModel):
    value: int = 0
    log: tuple[str, ...] = ()


class TransientError(Exception):
    pass


async def _pass(state: State) -> State:
    return state.model_copy(update={"value": state.value + 1})


def _one_node(node: FunctionNode[State]) -> Workflow[State]:
    return Workflow("one", State).add_node(node).set_entry(node.name).add_edge(node.name, END)


def _with_recovery(
    node: FunctionNode[State], recover: FunctionRecoveryNode[State]
) -> Workflow[State]:
    return (
        Workflow("recover", State)
        .add_node(node)
        .add_node(recover)
        .set_entry(node.name)
        .add_edge(node.name, END)
        .add_error_edge(node.name, recover.name)
        .add_edge(recover.name, END)
    )


async def test_default_runs_once_and_records_success(engine: WorkflowEngine) -> None:
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        return await _pass(state)

    result = await engine.execute(_one_node(FunctionNode("work", run)), State())
    assert calls == 1
    assert result.trace == ("work",)
    assert result.records[0].attempts == 1
    assert result.records[0].outcome is ExecutionOutcome.SUCCEEDED
    assert result.records[0].timeouts == 0


async def test_explicit_retry_succeeds_after_one_failure(engine: WorkflowEngine) -> None:
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientError("secret detail")
        return await _pass(state)

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=(TransientError,), initial_delay=0)
    )
    result = await engine.execute(_one_node(FunctionNode("work", run, policy=policy)), State())
    assert calls == 2
    assert result.state.value == 1
    assert result.trace == ("work",)
    assert result.records[0].attempts == 2
    assert result.records[0].retries == 1
    assert result.records[0].failure is None
    assert result.records[0].last_error_category is FailureCategory.NODE_ERROR
    assert result.records[0].last_exception_type == "TransientError"


async def test_retry_exhaustion_is_structured_and_bounded(engine: WorkflowEngine) -> None:
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        raise TransientError("secret detail")

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=(TransientError,), initial_delay=0)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(_one_node(FunctionNode("work", run, policy=policy)), State())
    assert calls == 3
    assert info.value.trace == ("work",)
    assert info.value.category is FailureCategory.NODE_ERROR
    assert info.value.failure == NodeFailure(
        "work", 3, FailureCategory.NODE_ERROR, "TransientError"
    )
    assert info.value.records[0].attempts == 3
    assert info.value.records[0].outcome is ExecutionOutcome.FAILED
    assert "secret detail" not in str(info.value)
    assert isinstance(info.value.__cause__, TransientError)


async def test_non_retryable_failure_runs_once(engine: WorkflowEngine) -> None:
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        raise ValueError("bad")

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=4, retry_on=(TransientError,), initial_delay=0)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(_one_node(FunctionNode("work", run, policy=policy)), State())
    assert calls == 1
    assert info.value.failure is not None
    assert info.value.failure.attempts == 1


@pytest.mark.parametrize(
    "error", [ConfigurationError("bad"), ValidationError.from_exception_data("x", [])]
)
async def test_configuration_and_validation_failures_do_not_retry(
    engine: WorkflowEngine, error: Exception
) -> None:
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        raise error

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=(ValueError,), initial_delay=0)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(_one_node(FunctionNode("work", run, policy=policy)), State())
    assert calls == 1
    expected = (
        FailureCategory.CONFIGURATION if isinstance(error, ConfigurationError)
        else FailureCategory.VALIDATION
    )
    assert info.value.category is expected


async def test_no_timeout_is_default(engine: WorkflowEngine) -> None:
    result = await engine.execute(_one_node(FunctionNode("work", _pass)), State())
    assert result.state.value == 1


async def test_node_completes_before_configured_timeout(engine: WorkflowEngine) -> None:
    node = FunctionNode("work", _pass, policy=NodeExecutionPolicy(timeout_seconds=1))
    result = await engine.execute(_one_node(node), State())
    assert result.records[0].timeouts == 0


async def test_timeout_is_framework_error_and_cancels_node(engine: WorkflowEngine) -> None:
    stopped = asyncio.Event()

    async def run(state: State) -> State:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        return state

    node = FunctionNode("work", run, policy=NodeExecutionPolicy(timeout_seconds=0.01))
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(_one_node(node), State())
    assert stopped.is_set()
    assert info.value.category is FailureCategory.TIMEOUT
    assert info.value.failure is not None
    assert (info.value.failure.attempts, info.value.failure.timeouts) == (1, 1)
    assert info.value.records[0].timeouts == 1


async def test_timeout_retry_is_explicit_and_bounded(engine: WorkflowEngine) -> None:
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
        return state

    node = FunctionNode(
        "work",
        run,
        policy=NodeExecutionPolicy(
            timeout_seconds=0.01,
            retry_policy=RetryPolicy(max_attempts=2, retry_timeouts=True, initial_delay=0),
        ),
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(_one_node(node), State())
    assert calls == 2
    assert info.value.category is FailureCategory.TIMEOUT
    assert info.value.failure is not None
    assert info.value.failure.timeouts == 2


async def test_success_uses_normal_route_even_with_recovery(engine: WorkflowEngine) -> None:
    recovery_calls = 0

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recovery_calls
        recovery_calls += 1
        return state

    wf = _with_recovery(FunctionNode("work", _pass), FunctionRecoveryNode("fix", recover))
    result = await engine.execute(wf, State())
    assert result.trace == ("work",)
    assert recovery_calls == 0
    assert result.records[0].outcome is ExecutionOutcome.SUCCEEDED


async def test_recovery_receives_structured_failure_after_retries(engine: WorkflowEngine) -> None:
    calls = 0
    seen: list[NodeFailure] = []

    async def fail(state: State) -> State:
        nonlocal calls
        calls += 1
        raise TransientError("private data")

    async def recover(state: State, failure: NodeFailure) -> State:
        seen.append(failure)
        return state.model_copy(update={"value": failure.attempts})

    node = FunctionNode(
        "work", fail,
        policy=NodeExecutionPolicy(
            retry_policy=RetryPolicy(max_attempts=2, retry_on=(TransientError,), initial_delay=0)
        ),
    )
    result = await engine.execute(
        _with_recovery(node, FunctionRecoveryNode("fix", recover)), State()
    )
    assert calls == 2
    assert result.state.value == 2
    assert result.trace == ("work", "fix")
    assert seen == [NodeFailure("work", 2, FailureCategory.NODE_ERROR, "TransientError")]
    assert result.records[0].outcome is ExecutionOutcome.ROUTED
    assert result.records[0].routed_to == "fix"
    assert result.records[1].outcome is ExecutionOutcome.SUCCEEDED
    assert "private data" not in repr(result.records)


async def test_recovery_can_fail_the_workflow(engine: WorkflowEngine) -> None:
    async def fail(state: State) -> State:
        raise TransientError("bad")

    async def recover(state: State, failure: NodeFailure) -> State:
        raise RuntimeError("recovery failed")

    wf = _with_recovery(FunctionNode("work", fail), FunctionRecoveryNode("fix", recover))
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(wf, State())
    assert info.value.node == "fix"
    assert info.value.trace == ("work", "fix")
    assert [r.outcome for r in info.value.records] == [
        ExecutionOutcome.ROUTED, ExecutionOutcome.FAILED
    ]


async def test_recovery_can_explicitly_finish_in_failed_state(engine: WorkflowEngine) -> None:
    class Status(BaseModel):
        failed: bool = False

    async def fail(state: Status) -> Status:
        raise TransientError("bad")

    async def recover(state: Status, failure: NodeFailure) -> Status:
        return Status(failed=True)

    wf = (
        Workflow("failed", Status)
        .add_node(FunctionNode("work", fail))
        .add_node(FunctionRecoveryNode("fix", recover))
        .set_entry("work")
        .add_edge("work", END)
        .add_error_edge("work", "fix")
        .add_edge("fix", END)
    )
    result = await engine.execute(wf, Status())
    assert result.state.failed
    assert result.records[0].outcome is ExecutionOutcome.ROUTED


async def test_error_route_skips_validation_failures(engine: WorkflowEngine) -> None:
    calls = 0

    async def fail(state: State) -> State:
        raise ConfigurationError("secret")

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal calls
        calls += 1
        return state

    wf = _with_recovery(FunctionNode("work", fail), FunctionRecoveryNode("fix", recover))
    with pytest.raises(NodeExecutionError):
        await engine.execute(wf, State())
    assert calls == 0


async def test_external_cancellation_propagates_without_retry_or_recovery(
    engine: WorkflowEngine,
) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()
    calls = 0
    recoveries = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        return state

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=(TransientError,), initial_delay=0)
    )
    wf = _with_recovery(
        FunctionNode("work", run, policy=policy), FunctionRecoveryNode("fix", recover)
    )
    task = asyncio.create_task(engine.execute(wf, State()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert calls == 1
    assert recoveries == 0


async def test_cancellation_during_backoff_stops_retries(
    engine: WorkflowEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    backoff_started = asyncio.Event()
    backoff_stopped = asyncio.Event()
    calls = 0
    recoveries = 0

    async def controlled_backoff(delay: float) -> None:
        backoff_started.set()
        assert delay == 60
        try:
            await asyncio.Event().wait()
        finally:
            backoff_stopped.set()

    monkeypatch.setattr(reliability, "_sleep_backoff", controlled_backoff)

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        raise TransientError("bad")

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(
            max_attempts=3, retry_on=(TransientError,), initial_delay=60, max_delay=60
        )
    )
    workflow = _with_recovery(
        FunctionNode("work", run, policy=policy), FunctionRecoveryNode("fix", recover)
    )
    task = asyncio.create_task(engine.execute(workflow, State()))
    await backoff_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backoff_stopped.is_set()
    assert calls == 1
    assert recoveries == 0


async def test_step_limit_records_previous_work(engine: WorkflowEngine) -> None:
    wf = (
        Workflow("loop", State)
        .add_node(FunctionNode("work", _pass))
        .set_entry("work")
        .add_conditional_edges("work", lambda state: "again", {"again": "work"})
    )
    with pytest.raises(WorkflowExecutionError) as info:
        await engine.execute(wf, State(), config=ExecutionConfig(max_steps=2))
    assert info.value.category is FailureCategory.STEP_LIMIT
    assert len(info.value.records) == 2


async def test_wrong_return_type_does_not_route_to_recovery(engine: WorkflowEngine) -> None:
    recoveries = 0

    async def wrong(state: State) -> State:
        return cast(State, "wrong")

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    wf = _with_recovery(FunctionNode("work", wrong), FunctionRecoveryNode("fix", recover))
    with pytest.raises(NodeExecutionError):
        await engine.execute(wf, State())
    assert recoveries == 0


async def test_timeout_then_success_records_the_timeout(engine: WorkflowEngine) -> None:
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.Event().wait()
        return await _pass(state)

    node = FunctionNode(
        "work", run,
        policy=NodeExecutionPolicy(
            timeout_seconds=0.01,
            retry_policy=RetryPolicy(max_attempts=2, retry_timeouts=True, initial_delay=0),
        ),
    )
    result = await engine.execute(_one_node(node), State())
    assert calls == 2
    assert result.state.value == 1
    assert result.records[0].attempts == 2
    assert result.records[0].timeouts == 1
    assert result.records[0].last_error_category is FailureCategory.TIMEOUT


async def test_conditional_success_and_error_routes_are_distinct(engine: WorkflowEngine) -> None:
    async def work(state: State) -> State:
        if state.value < 0:
            raise TransientError("bad")
        return await _pass(state)

    async def recover(state: State, failure: NodeFailure) -> State:
        return state.model_copy(update={"log": (failure.node,)})

    wf = (
        Workflow("conditional", State)
        .add_node(FunctionNode("work", work))
        .add_node(FunctionNode("next", _pass))
        .add_node(FunctionRecoveryNode("fix", recover))
        .set_entry("work")
        .add_conditional_edges("work", lambda state: "next", {"next": "next"})
        .add_error_edge("work", "fix")
        .add_edge("next", END)
        .add_edge("fix", END)
    )
    success = await engine.execute(wf, State())
    failure = await engine.execute(wf, State(value=-1))
    assert success.trace == ("work", "next")
    assert failure.trace == ("work", "fix")
    assert failure.state.log == ("work",)


async def test_tool_result_failure_does_not_trigger_node_retry(engine: WorkflowEngine) -> None:
    tools = ToolRegistry()
    tools.register(FakeSearchTool())
    calls = 0

    async def run(state: State) -> State:
        nonlocal calls
        calls += 1
        result = await tools.execute("search", {"query": "boom"})
        assert not result.ok
        return state

    node = FunctionNode(
        "work", run,
        policy=NodeExecutionPolicy(
            retry_policy=RetryPolicy(max_attempts=3, retry_on=(RuntimeError,), initial_delay=0)
        ),
    )
    result = await engine.execute(_one_node(node), State())
    assert calls == 1
    assert result.records[0].attempts == 1


async def test_concurrent_runs_keep_recovery_context_separate(engine: WorkflowEngine) -> None:
    both_started = asyncio.Event()
    count = 0

    async def fail(state: State) -> State:
        nonlocal count
        count += 1
        if count == 2:
            both_started.set()
        await both_started.wait()
        raise TransientError("bad")

    async def recover(state: State, failure: NodeFailure) -> State:
        return state.model_copy(update={"log": (f"{state.value}:{failure.node}",)})

    wf = _with_recovery(FunctionNode("work", fail), FunctionRecoveryNode("fix", recover))
    first, second = await asyncio.gather(
        engine.execute(wf, State(value=1)), engine.execute(wf, State(value=2))
    )
    assert first.state.log == ("1:work",)
    assert second.state.log == ("2:work",)
    assert all(result.records[0].routed_to == "fix" for result in (first, second))


async def test_node_cannot_turn_expired_timeout_into_success(engine: WorkflowEngine) -> None:
    async def suppress_cancellation(state: State) -> State:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return state
        return state

    node = FunctionNode(
        "work", suppress_cancellation, policy=NodeExecutionPolicy(timeout_seconds=0.01)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(_one_node(node), State())
    assert info.value.category is FailureCategory.TIMEOUT
    assert info.value.failure is not None
    assert info.value.failure.timeouts == 1


async def test_node_cannot_turn_external_cancellation_into_success(
    engine: WorkflowEngine,
) -> None:
    started = asyncio.Event()

    async def suppress_cancellation(state: State) -> State:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return state
        return state

    task = asyncio.create_task(
        engine.execute(_one_node(FunctionNode("work", suppress_cancellation)), State())
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class MutableState(BaseModel):
    payload: dict[str, list[str]] = Field(default_factory=lambda: {"events": list[str]()})


async def test_retry_uses_clean_nested_state_and_keeps_successful_update(
    engine: WorkflowEngine,
) -> None:
    initial = MutableState()
    seen: list[list[str]] = []

    async def run(state: MutableState) -> MutableState:
        seen.append(list(state.payload["events"]))
        state.payload["events"].append("changed")
        if len(seen) == 1:
            raise TransientError("retry")
        return state

    workflow = (
        Workflow("nested-retry", MutableState)
        .add_node(FunctionNode("work", run, policy=NodeExecutionPolicy(
            retry_policy=RetryPolicy(max_attempts=2, retry_on=(TransientError,), initial_delay=0)
        )))
        .set_entry("work")
        .add_edge("work", END)
    )
    result = await engine.execute(workflow, initial)
    assert seen == [[], []]
    assert initial.payload == {"events": []}
    assert result.state.payload == {"events": ["changed"]}
    assert result.records[0].attempts == 2


async def test_recovery_receives_last_committed_nested_state(engine: WorkflowEngine) -> None:
    initial = MutableState()
    recovered_from: list[list[str]] = []

    async def commit(state: MutableState) -> MutableState:
        state.payload["events"].append("committed")
        return state

    async def fail(state: MutableState) -> MutableState:
        state.payload["events"].append("failed attempt")
        raise TransientError("route")

    async def recover(state: MutableState, failure: NodeFailure) -> MutableState:
        recovered_from.append(list(state.payload["events"]))
        state.payload["events"].append("recovered")
        return state

    workflow = (
        Workflow("nested-recovery", MutableState)
        .add_node(FunctionNode("commit", commit))
        .add_node(FunctionNode("work", fail))
        .add_node(FunctionRecoveryNode("fix", recover))
        .set_entry("commit")
        .add_edge("commit", "work")
        .add_edge("work", END)
        .add_error_edge("work", "fix")
        .add_edge("fix", END)
    )
    result = await engine.execute(workflow, initial)
    assert recovered_from == [["committed"]]
    assert initial.payload == {"events": []}
    assert result.state.payload == {"events": ["committed", "recovered"]}
    assert result.trace == ("commit", "work", "fix")
    assert result.records[1].outcome is ExecutionOutcome.ROUTED


async def test_invalid_model_copy_output_is_framework_error(engine: WorkflowEngine) -> None:
    calls = 0
    recoveries = 0

    async def invalid(state: State) -> State:
        nonlocal calls
        calls += 1
        return state.model_copy(update={"value": "invalid"})

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=(ValidationError,), initial_delay=0)
    )
    workflow = _with_recovery(
        FunctionNode("work", invalid, policy=policy), FunctionRecoveryNode("fix", recover)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(workflow, State())
    assert calls == 1
    assert recoveries == 0
    assert info.value.node == "work"
    assert info.value.category is FailureCategory.INVALID_STATE
    assert info.value.trace == ("work",)
    assert len(info.value.records) == 1
    record = info.value.records[0]
    assert record.attempts == 1
    assert record.outcome is ExecutionOutcome.FAILED
    assert record.failure is not None
    assert record.failure.category is FailureCategory.INVALID_STATE


@pytest.mark.parametrize("category", [
    FailureCategory.STEP_LIMIT,
    FailureCategory.ROUTING,
    FailureCategory.INVALID_STATE,
    FailureCategory.VALIDATION,
])
async def test_nested_framework_error_keeps_category_and_skips_retry_and_recovery(
    engine: WorkflowEngine, category: FailureCategory
) -> None:
    calls = 0
    recoveries = 0

    async def fail(state: State) -> State:
        nonlocal calls
        calls += 1
        raise WorkflowExecutionError("internal", category=category)

    async def recover(state: State, failure: NodeFailure) -> State:
        nonlocal recoveries
        recoveries += 1
        return state

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(
            max_attempts=3, retry_on=(WorkflowExecutionError,), initial_delay=0
        )
    )
    workflow = _with_recovery(
        FunctionNode("work", fail, policy=policy), FunctionRecoveryNode("fix", recover)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(workflow, State())
    assert calls == 1
    assert recoveries == 0
    assert info.value.node == "work"
    assert info.value.category is category
    assert info.value.trace == ("work",)
    assert len(info.value.records) == 1
    assert info.value.records[0].failure is not None
    assert info.value.records[0].failure.category is category


class Inner(BaseModel):
    value: int


class NestedState(BaseModel):
    inner: Inner


async def test_nested_pydantic_output_is_revalidated(engine: WorkflowEngine) -> None:
    async def invalid(state: NestedState) -> NestedState:
        return state.model_copy(
            update={"inner": state.inner.model_copy(update={"value": "invalid"})}
        )

    workflow = (
        Workflow("invalid-nested", NestedState)
        .add_node(FunctionNode("work", invalid))
        .set_entry("work")
        .add_edge("work", END)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(workflow, NestedState(inner=Inner(value=1)))
    assert info.value.category is FailureCategory.INVALID_STATE
    assert info.value.records[0].outcome is ExecutionOutcome.FAILED


async def test_excluded_pydantic_field_is_preserved_and_validated(engine: WorkflowEngine) -> None:
    class HiddenState(BaseModel):
        value: int = 0
        hidden: int = Field(default=7, exclude=True)

    async def valid(state: HiddenState) -> HiddenState:
        return state.model_copy(update={"value": 1})

    valid_workflow = (
        Workflow("hidden-valid", HiddenState)
        .add_node(FunctionNode("work", valid))
        .set_entry("work")
        .add_edge("work", END)
    )
    result = await engine.execute(valid_workflow, HiddenState(hidden=9))
    assert result.state.value == 1
    assert result.state.hidden == 9

    async def invalid(state: HiddenState) -> HiddenState:
        return state.model_copy(update={"hidden": "invalid"})

    invalid_workflow = (
        Workflow("hidden-invalid", HiddenState)
        .add_node(FunctionNode("work", invalid))
        .set_entry("work")
        .add_edge("work", END)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(invalid_workflow, HiddenState())
    assert info.value.category is FailureCategory.INVALID_STATE
