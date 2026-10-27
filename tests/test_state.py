from __future__ import annotations

import pytest
from pydantic import ValidationError

from maximusforge.agents import AgentState, AgentStatus, ExecutionStep
from maximusforge.core import InvalidStateTransitionError, Task
from maximusforge.planning import Plan, PlanStep


def _state() -> AgentState:
    return AgentState.for_task(Task(goal="g"))


def test_task_requires_goal() -> None:
    with pytest.raises(ValidationError):
        Task(goal="")


def test_new_state_is_pending_and_not_terminal() -> None:
    state = _state()
    assert state.status is AgentStatus.PENDING
    assert not state.is_terminal


def test_happy_path_transitions() -> None:
    state = _state().transition(AgentStatus.RUNNING).complete("done")
    assert state.status is AgentStatus.COMPLETED
    assert state.output == "done"
    assert state.is_terminal


def test_fail_records_error() -> None:
    state = _state().transition(AgentStatus.RUNNING).fail("bad")
    assert (state.status, state.error) == (AgentStatus.FAILED, "bad")


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (AgentStatus.PENDING, AgentStatus.COMPLETED),
        (AgentStatus.RUNNING, AgentStatus.PENDING),
        (AgentStatus.COMPLETED, AgentStatus.RUNNING),
        (AgentStatus.FAILED, AgentStatus.COMPLETED),
    ],
)
def test_illegal_transitions_raise(start: AgentStatus, target: AgentStatus) -> None:
    state = _state().model_copy(update={"status": start})
    with pytest.raises(InvalidStateTransitionError):
        state.transition(target)


def test_state_is_immutable_and_updates_return_new_values() -> None:
    original = _state()
    updated = original.with_plan(Plan(goal="g", steps=(PlanStep(description="s"),)))
    assert original.plan is None
    assert updated.plan is not None
    field = "status"
    with pytest.raises(ValidationError):
        setattr(original, field, AgentStatus.RUNNING)


def test_with_step_appends_in_order() -> None:
    state = (
        _state().with_step(ExecutionStep(description="a")).with_step(ExecutionStep(description="b"))
    )
    assert [s.description for s in state.steps] == ["a", "b"]
