"""The boundary between workflow definitions and whatever executes them (ADR-006).

Everything outside ``maximusforge.workflows.engines`` depends on :class:`WorkflowEngine` only.
A native MaximusForge graph engine will be a second implementation of this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic

from maximusforge.workflows.workflow import S, Workflow


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    max_steps: int = 25
    """Upper bound on node executions in one run; guards against routing loops."""

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")


@dataclass(frozen=True, slots=True)
class WorkflowResult(Generic[S]):
    state: S
    trace: tuple[str, ...] = field(default=())
    """Names of the nodes that executed, in order."""


class WorkflowEngine(ABC):
    """Executes a :class:`Workflow` from an initial state to completion.

    Contract for implementations:

    * validate the workflow first (``workflow.validate()``);
    * follow the entry node, edges and conditional routes until ``END``;
    * raise :class:`~maximusforge.core.errors.NodeExecutionError` if a node raises, chaining
      the original exception and populating ``trace``;
    * raise :class:`~maximusforge.core.errors.WorkflowExecutionError` if ``max_steps`` is
      exceeded or a router returns an undeclared route;
    * not retain state between calls.
    """

    @abstractmethod
    async def execute(
        self,
        workflow: Workflow[S],
        initial_state: S,
        *,
        config: ExecutionConfig | None = None,
    ) -> WorkflowResult[S]: ...
