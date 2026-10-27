"""AgentState: the working memory of one agent run, and the workflow state type for agents.

Immutable: every change returns a new state. Reasoning is represented operationally (plan,
tool calls, observations), never as stored model chain-of-thought.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maximusforge.core.errors import InvalidStateTransitionError
from maximusforge.core.task import Task
from maximusforge.planning.plan import Plan


class AgentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


_ALLOWED: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.PENDING: frozenset({AgentStatus.RUNNING, AgentStatus.FAILED}),
    AgentStatus.RUNNING: frozenset({AgentStatus.COMPLETED, AgentStatus.FAILED}),
    AgentStatus.COMPLETED: frozenset(),
    AgentStatus.FAILED: frozenset(),
}


class ExecutionStep(BaseModel):
    """One action taken and what came of it."""

    model_config = ConfigDict(frozen=True)

    description: str
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    observation: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class AgentState(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: Task
    status: AgentStatus = AgentStatus.PENDING
    plan: Plan | None = None
    steps: tuple[ExecutionStep, ...] = ()
    output: str | None = None
    error: str | None = None

    @classmethod
    def for_task(cls, task: Task) -> AgentState:
        return cls(task=task)

    @property
    def is_terminal(self) -> bool:
        return not _ALLOWED[self.status]

    def transition(self, to: AgentStatus) -> AgentState:
        if to not in _ALLOWED[self.status]:
            raise InvalidStateTransitionError(f"cannot move from {self.status.value} to {to.value}")
        return self.model_copy(update={"status": to})

    def with_plan(self, plan: Plan) -> AgentState:
        return self.model_copy(update={"plan": plan})

    def with_step(self, step: ExecutionStep) -> AgentState:
        return self.model_copy(update={"steps": (*self.steps, step)})

    def complete(self, output: str) -> AgentState:
        return self.transition(AgentStatus.COMPLETED).model_copy(update={"output": output})

    def fail(self, error: str) -> AgentState:
        return self.transition(AgentStatus.FAILED).model_copy(update={"error": error})
