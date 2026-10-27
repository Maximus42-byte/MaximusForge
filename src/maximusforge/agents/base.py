"""The Agent base class: owns a workflow definition and runs it on an injected engine."""

from __future__ import annotations

from abc import ABC, abstractmethod

from maximusforge.agents.state import AgentState, AgentStatus
from maximusforge.core.errors import AgentError
from maximusforge.core.task import Task
from maximusforge.workflows.engine import ExecutionConfig, WorkflowEngine, WorkflowResult
from maximusforge.workflows.workflow import Workflow


class Agent(ABC):
    """An agent is a named workflow over :class:`AgentState` plus the dependencies its nodes need.

    Agents see only the :class:`WorkflowEngine` interface, never a concrete graph backend.
    Subclasses implement :meth:`build_workflow`; dependencies (LLM, tools, memory, ...) are
    constructor-injected.
    """

    def __init__(self, name: str, engine: WorkflowEngine) -> None:
        self.name = name
        self._engine = engine

    @abstractmethod
    def build_workflow(self) -> Workflow[AgentState]:
        """Return this agent's workflow. Called once per run, so it must be cheap and pure."""

    async def run(
        self, task: Task, *, config: ExecutionConfig | None = None
    ) -> WorkflowResult[AgentState]:
        """Run ``task`` to a terminal state.

        Raises :class:`AgentError` if the workflow ends without completing or failing the state;
        node and engine failures propagate as :class:`~maximusforge.core.errors.WorkflowError`.
        """
        initial = AgentState.for_task(task).transition(AgentStatus.RUNNING)
        result = await self._engine.execute(self.build_workflow(), initial, config=config)
        if not result.state.is_terminal:
            raise AgentError(
                f"agent {self.name!r} finished its workflow in non-terminal status "
                f"{result.state.status.value!r}"
            )
        return result
