"""LangGraph-backed WorkflowEngine; the sole LangGraph integration module."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, cast

from langchain_core.runnables import RunnableLambda
from langgraph.errors import GraphRecursionError
from langgraph.graph import END as LG_END
from langgraph.graph import StateGraph
from pydantic import BaseModel, TypeAdapter

from maximusforge.core.errors import (
    ExecutionOutcome,
    ExecutionRecord,
    FailureCategory,
    NodeExecutionError,
    NodeFailure,
    WorkflowExecutionError,
)
from maximusforge.events.sink import _RunEventEmitter
from maximusforge.events.types import ExecutionEventType
from maximusforge.workflows.engine import ExecutionConfig, WorkflowEngine, WorkflowResult
from maximusforge.workflows.reliability import _category, _is_recoverable, _run_attempts
from maximusforge.workflows.workflow import END, RecoveryNode, S, Workflow, WorkflowNode


@dataclass(slots=True)
class _RunContext:
    """Mutable, private bookkeeping for one execute call only."""

    max_steps: int
    trace: list[str] = field(default_factory=list)
    records: list[ExecutionRecord] = field(default_factory=list)
    route_failures: dict[str, NodeFailure] = field(default_factory=dict)
    pending_recovery: dict[str, NodeFailure] = field(default_factory=dict)


class _NodeAttemptObserver:
    """Translate authoritative retry transitions into public node events."""

    def __init__(
        self, events: _RunEventEmitter, node_name: str, recovery_for: str | None
    ) -> None:
        self.events = events
        self.node_name = node_name
        self.recovery_for = recovery_for

    async def started(self, attempt: int) -> None:
        await self.events.emit(
            ExecutionEventType.NODE_STARTED,
            node_name=self.node_name,
            attempt=attempt,
            recovery_for=self.recovery_for,
        )

    async def failed(
        self, attempt: int, category: FailureCategory, duration_seconds: float
    ) -> None:
        await self.events.emit(
            ExecutionEventType.NODE_ATTEMPT_FAILED,
            node_name=self.node_name,
            attempt=attempt,
            category=category,
            duration_seconds=duration_seconds,
            recovery_for=self.recovery_for,
        )

    async def retry_scheduled(
        self, attempt: int, next_attempt: int, category: FailureCategory, delay_seconds: float
    ) -> None:
        await self.events.emit(
            ExecutionEventType.NODE_RETRY_SCHEDULED,
            node_name=self.node_name,
            attempt=attempt,
            category=category,
            next_attempt=next_attempt,
            retry_delay_seconds=delay_seconds,
            recovery_for=self.recovery_for,
        )


class LangGraphEngine(WorkflowEngine):
    """Compile a workflow per call, keeping policies and recovery state run-local."""

    async def execute(
        self,
        workflow: Workflow[S],
        initial_state: S,
        *,
        config: ExecutionConfig | None = None,
    ) -> WorkflowResult[S]:
        config = config or ExecutionConfig()
        events = _RunEventEmitter(workflow.name, config.event_sink)
        started_at = time.perf_counter()
        try:
            await events.emit(ExecutionEventType.WORKFLOW_STARTED)
            workflow.validate()
            if not isinstance(initial_state, workflow.state_type):
                raise WorkflowExecutionError(
                    "initial state has the wrong type", category=FailureCategory.INVALID_STATE
                )
            context = _RunContext(max_steps=config.max_steps)
            compiled = self._compile(workflow, context, events)
            try:
                output = await compiled.ainvoke(
                    initial_state, config={"recursion_limit": config.max_steps + 1}
                )
            except GraphRecursionError as exc:
                raise WorkflowExecutionError(
                    f"workflow {workflow.name!r} exceeded max_steps={config.max_steps}",
                    trace=context.trace,
                    records=context.records,
                    category=FailureCategory.STEP_LIMIT,
                ) from exc
            result = WorkflowResult(
                state=_coerce(workflow.state_type, output),
                trace=tuple(context.trace),
                records=tuple(context.records),
                run_id=events.run_id,
            )
            await events.emit(
                ExecutionEventType.WORKFLOW_COMPLETED,
                duration_seconds=time.perf_counter() - started_at,
            )
            return result
        except asyncio.CancelledError:
            await events.emit_cancelled(duration_seconds=time.perf_counter() - started_at)
            raise
        except Exception as exc:
            await events.emit(
                ExecutionEventType.WORKFLOW_FAILED,
                category=_category(exc, False),
                duration_seconds=time.perf_counter() - started_at,
            )
            raise

    def _compile(
        self, workflow: Workflow[S], context: _RunContext, events: _RunEventEmitter
    ) -> Any:
        graph = StateGraph(workflow.state_type)
        for name, node in workflow.nodes.items():
            error_edge = workflow.error_edges.get(name)
            graph.add_node(
                name,
                RunnableLambda(
                    _wrap_node(
                        workflow.state_type,
                        node,
                        context,
                        error_edge.target if error_edge is not None else None,
                        events,
                    )
                ),
            )
        graph.set_entry_point(workflow.entry)
        for name in workflow.nodes:
            edge = workflow.edges.get(name)
            conditional = workflow.conditional_edges.get(name)
            error_edge = workflow.error_edges.get(name)
            if edge is not None:
                normal_targets = {edge.target}
            elif conditional is not None:
                normal_targets = set(conditional.routes.values())
            else:
                normal_targets = set()
            targets = normal_targets | ({error_edge.target} if error_edge is not None else set())
            graph.add_conditional_edges(
                name,
                _wrap_router(
                    workflow.state_type,
                    name,
                    edge.target if edge is not None else None,
                    conditional.router if conditional is not None else None,
                    conditional.routes if conditional is not None else None,
                    error_edge.target if error_edge is not None else None,
                    context,
                    events,
                ),
                {target: _target(target) for target in targets},
            )
        return graph.compile()


def _target(name: str) -> str:
    return LG_END if name == END else name


def _coerce(state_type: type[S], value: object) -> S:
    if isinstance(value, state_type):
        return value
    return state_type.model_validate(value)


class _WrongStateTypeError(WorkflowExecutionError, TypeError):
    """A wrong return type is both a type error and a framework state failure."""


def _restore_nested_models(raw: object, dumped: Any) -> Any:
    if isinstance(raw, BaseModel):
        return _round_trip_data(raw)
    if isinstance(raw, dict) and isinstance(dumped, dict):
        return {
            key: _restore_nested_models(raw[key], item) if key in raw else item
            for key, item in dumped.items()
        }
    if isinstance(raw, (list, tuple)) and isinstance(dumped, (list, tuple)):
        return type(dumped)(
            _restore_nested_models(raw[index], item) if index < len(raw) else item
            for index, item in enumerate(dumped)
        )
    return dumped


def _round_trip_data(state: BaseModel) -> dict[str, Any]:
    """Preserve excluded fields while converting nested models to validation data."""
    data = state.model_dump(mode="python", round_trip=True, by_alias=True, warnings="error")
    for name, model_field in type(state).model_fields.items():
        raw = getattr(state, name)
        serialized_key = model_field.serialization_alias or model_field.alias or name
        validation_alias = model_field.validation_alias
        validation_key = (
            validation_alias if isinstance(validation_alias, str) else model_field.alias or name
        )
        if serialized_key in data:
            dumped = data.pop(serialized_key)
        else:
            # Field(exclude=True) is absent from model_dump but still belongs to state.
            dumped = TypeAdapter(model_field.annotation or Any).dump_python(
                raw, mode="python", round_trip=True, warnings="error"
            )
        data[validation_key] = _restore_nested_models(raw, dumped)
    return data


def _validate_node_output(state_type: type[S], value: object) -> S:
    """Revalidate returned fields, including nested models skipped for model instances."""
    if not isinstance(value, state_type):
        raise _WrongStateTypeError(
            "node returned the wrong state type", category=FailureCategory.INVALID_STATE
        )
    try:
        # model_validate(value) trusts existing instances by default. Round-trip data
        # forces field validation, including nested models and excluded fields.
        return state_type.model_validate(_round_trip_data(value))
    except Exception as exc:
        raise WorkflowExecutionError(
            "node returned an invalid state", category=FailureCategory.INVALID_STATE
        ) from exc


def _as_update(state: BaseModel) -> dict[str, Any]:
    # Shallow field dict preserves nested Pydantic models across LangGraph updates.
    return {name: getattr(state, name) for name in type(state).model_fields}


def _wrap_node(
    state_type: type[S],
    node: WorkflowNode[S],
    context: _RunContext,
    error_target: str | None,
    events: _RunEventEmitter,
) -> Callable[[BaseModel], Awaitable[dict[str, Any]]]:
    async def execute_node(state: BaseModel) -> dict[str, Any]:
        if len(context.trace) >= context.max_steps:
            raise WorkflowExecutionError(
                f"workflow exceeded max_steps={context.max_steps}",
                trace=context.trace,
                records=context.records,
                category=FailureCategory.STEP_LIMIT,
            )
        name = node.name
        context.trace.append(name)
        current = _coerce(state_type, state)
        prior_failure = context.pending_recovery.pop(name, None)
        if isinstance(node, RecoveryNode) and prior_failure is None:
            raise WorkflowExecutionError(
                f"recovery node {name!r} has no failure context",
                trace=context.trace,
                records=context.records,
                category=FailureCategory.ROUTING,
            )

        async def run_node() -> S:
            try:
                # Each attempt receives its own copy. Failed mutations never touch the
                # committed state used by later attempts and recovery routing.
                attempt_state = current.model_copy(deep=True)
            except Exception as exc:
                raise WorkflowExecutionError(
                    "node state could not be isolated", category=FailureCategory.INVALID_STATE
                ) from exc
            if isinstance(node, RecoveryNode):
                assert prior_failure is not None
                value = cast(S, await node.recover(attempt_state, prior_failure))
            else:
                value = await node.run(attempt_state)
            return _validate_node_output(state_type, value)

        recovery_for = prior_failure.node if prior_failure is not None else None
        started_at = time.perf_counter()
        outcome = await _run_attempts(
            run_node,
            node.policy,
            observer=_NodeAttemptObserver(events, name, recovery_for),
        )
        error = outcome.error
        if error is not None:
            failure = NodeFailure(
                node=name,
                attempts=outcome.attempts,
                category=outcome.category or FailureCategory.NODE_ERROR,
                exception_type=type(error).__name__,
                timeouts=outcome.timeouts,
            )
            context.records.append(
                ExecutionRecord(
                    node=name,
                    attempts=outcome.attempts,
                    outcome=ExecutionOutcome.FAILED,
                    timeouts=outcome.timeouts,
                    failure=failure,
                    last_error_category=outcome.last_error_category,
                    last_exception_type=outcome.last_exception_type,
                )
            )
            await events.emit(
                ExecutionEventType.NODE_FAILED,
                node_name=name,
                attempt=outcome.attempts,
                category=failure.category,
                duration_seconds=time.perf_counter() - started_at,
                recovery_for=recovery_for,
            )
            if error_target is not None and _is_recoverable(error):
                context.route_failures[name] = failure
                return _as_update(current)
            raise NodeExecutionError(
                name,
                failure.exception_type,
                trace=context.trace,
                records=context.records,
                failure=failure,
            ) from error
        context.records.append(
            ExecutionRecord(
                node=name,
                attempts=outcome.attempts,
                outcome=ExecutionOutcome.SUCCEEDED,
                timeouts=outcome.timeouts,
                last_error_category=outcome.last_error_category,
                last_exception_type=outcome.last_exception_type,
            )
        )
        await events.emit(
            ExecutionEventType.NODE_COMPLETED,
            node_name=name,
            attempt=outcome.attempts,
            duration_seconds=time.perf_counter() - started_at,
            recovery_for=recovery_for,
        )
        assert isinstance(outcome.value, state_type)
        return _as_update(outcome.value)

    return execute_node


def _wrap_router(
    state_type: type[S],
    source: str,
    normal_target: str | None,
    router: Callable[[S], str] | None,
    routes: Mapping[str, str] | None,
    error_target: str | None,
    context: _RunContext,
    events: _RunEventEmitter,
) -> Callable[[S], Awaitable[str]]:
    async def route(state: S) -> str:
        failure = context.route_failures.pop(source, None)
        if failure is not None:
            if error_target is None:
                raise AssertionError("missing error target for routed failure")
            context.pending_recovery[error_target] = failure
            context.records[-1] = replace(
                context.records[-1], outcome=ExecutionOutcome.ROUTED, routed_to=error_target
            )
            await events.emit(
                ExecutionEventType.NODE_RECOVERY_ROUTED,
                node_name=source,
                attempt=failure.attempts,
                category=failure.category,
                recovery_target=error_target,
            )
            return error_target
        if router is None:
            if normal_target is None:
                raise AssertionError("validated workflow has no normal target")
            return normal_target
        try:
            key = router(_coerce(state_type, state))
        except Exception as exc:
            raise WorkflowExecutionError(
                f"router after {source!r} raised {type(exc).__name__}",
                trace=context.trace,
                records=context.records,
                category=FailureCategory.ROUTING,
            ) from exc
        if routes is None or key not in routes:
            raise WorkflowExecutionError(
                f"router after {source!r} returned an undeclared route",
                trace=context.trace,
                records=context.records,
                category=FailureCategory.ROUTING,
            )
        return routes[key]

    return route
