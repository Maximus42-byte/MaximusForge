"""Contract tests for WorkflowEngine implementations (run against the `engine` fixture)."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import BaseModel

from maximusforge.core import NodeExecutionError, WorkflowExecutionError, WorkflowValidationError
from maximusforge.workflows import END, ExecutionConfig, FunctionNode, Workflow, WorkflowEngine


class Counter(BaseModel):
    n: int = 0
    log: tuple[str, ...] = ()


def _step(name: str, delta: int = 1) -> FunctionNode[Counter]:
    async def fn(state: Counter) -> Counter:
        return state.model_copy(update={"n": state.n + delta, "log": (*state.log, name)})

    return FunctionNode(name, fn)


async def test_linear_graph_executes_in_order(engine: WorkflowEngine) -> None:
    wf = (
        Workflow("linear", Counter)
        .add_node(_step("a"))
        .add_node(_step("b", 10))
        .set_entry("a")
        .add_edge("a", "b")
        .add_edge("b", END)
    )
    result = await engine.execute(wf, Counter())
    assert result.state == Counter(n=11, log=("a", "b"))
    assert result.trace == ("a", "b")


async def test_conditional_routing_selects_branch(engine: WorkflowEngine) -> None:
    def build() -> Workflow[Counter]:
        return (
            Workflow("branch", Counter)
            .add_node(_step("start"))
            .add_node(_step("big", 100))
            .add_node(_step("small", 5))
            .set_entry("start")
            .add_conditional_edges(
                "start", lambda s: "big" if s.n >= 10 else "small", {"big": "big", "small": "small"}
            )
            .add_edge("big", END)
            .add_edge("small", END)
        )

    low = await engine.execute(build(), Counter(n=0))
    high = await engine.execute(build(), Counter(n=50))
    assert low.trace == ("start", "small")
    assert high.trace == ("start", "big")


async def test_conditional_loop_until_condition(engine: WorkflowEngine) -> None:
    wf = (
        Workflow("loop", Counter)
        .add_node(_step("inc"))
        .set_entry("inc")
        .add_conditional_edges(
            "inc", lambda s: "again" if s.n < 3 else "done", {"again": "inc", "done": END}
        )
    )
    result = await engine.execute(wf, Counter())
    assert result.state.n == 3
    assert result.trace == ("inc", "inc", "inc")


async def test_initial_state_is_not_mutated(engine: WorkflowEngine) -> None:
    wf = Workflow("w", Counter).add_node(_step("a")).set_entry("a").add_edge("a", END)
    initial = Counter()
    await engine.execute(wf, initial)
    assert initial == Counter()


async def test_node_failure_wraps_cause_and_reports_trace(engine: WorkflowEngine) -> None:
    async def explode(state: Counter) -> Counter:
        raise ValueError("kaboom")

    wf = (
        Workflow("fail", Counter)
        .add_node(_step("ok"))
        .add_node(FunctionNode("bad", explode))
        .set_entry("ok")
        .add_edge("ok", "bad")
        .add_edge("bad", END)
    )
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(wf, Counter())
    assert info.value.node == "bad"
    assert info.value.trace == ("ok", "bad")
    assert isinstance(info.value.__cause__, ValueError)


async def test_max_steps_guards_infinite_loops(engine: WorkflowEngine) -> None:
    wf = (
        Workflow("spin", Counter)
        .add_node(_step("a"))
        .set_entry("a")
        .add_conditional_edges("a", lambda s: "again", {"again": "a"})
    )
    with pytest.raises(WorkflowExecutionError, match="max_steps") as info:
        await engine.execute(wf, Counter(), config=ExecutionConfig(max_steps=5))
    assert info.value.trace == ("a",) * 5


async def test_max_steps_allows_exactly_that_many_nodes(engine: WorkflowEngine) -> None:
    wf = (
        Workflow("two", Counter)
        .add_node(_step("a"))
        .add_node(_step("b"))
        .set_entry("a")
        .add_edge("a", "b")
        .add_edge("b", END)
    )
    result = await engine.execute(wf, Counter(), config=ExecutionConfig(max_steps=2))
    assert result.trace == ("a", "b")


def test_max_steps_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_steps"):
        ExecutionConfig(max_steps=0)


async def test_wrong_node_state_type_is_wrapped(engine: WorkflowEngine) -> None:
    async def wrong(state: Counter) -> Counter:
        return cast(Counter, "wrong")

    wf = Workflow("wrong", Counter).add_node(FunctionNode("bad", wrong)).set_entry("bad")
    wf.add_edge("bad", END)
    with pytest.raises(NodeExecutionError) as info:
        await engine.execute(wf, Counter())
    assert info.value.trace == ("bad",)
    assert isinstance(info.value.__cause__, TypeError)


async def test_undeclared_route_is_an_execution_error(engine: WorkflowEngine) -> None:
    wf = (
        Workflow("bad-route", Counter)
        .add_node(_step("a"))
        .set_entry("a")
        .add_conditional_edges("a", lambda s: "nowhere", {"somewhere": END})
    )
    with pytest.raises(WorkflowExecutionError, match="undeclared route"):
        await engine.execute(wf, Counter())


async def test_invalid_workflow_is_rejected_before_running(engine: WorkflowEngine) -> None:
    with pytest.raises(WorkflowValidationError):
        await engine.execute(Workflow("empty", Counter), Counter())
