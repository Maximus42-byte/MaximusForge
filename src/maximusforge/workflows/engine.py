"""The boundary between workflow definitions and whatever executes them (ADR-006).

Everything outside ``maximusforge.workflows.engines`` depends on :class:`WorkflowEngine` only.
A native MaximusForge graph engine will be a second implementation of this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic
from uuid import UUID

from maximusforge.core.errors import ExecutionRecord
from maximusforge.events import EventSink
from maximusforge.workflows.workflow import S, Workflow


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    max_steps: int = 25
    """Upper bound on node executions in one run; guards against routing loops."""

    event_sink: EventSink | None = field(default=None, compare=False, repr=False)
    """Optional, per-run structured event receiver."""

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if self.event_sink is not None and not isinstance(self.event_sink, EventSink):
            raise ValueError("event_sink must be an EventSink")


@dataclass(frozen=True, slots=True)
class WorkflowResult(Generic[S]):
    state: S
    trace: tuple[str, ...] = field(default=())
    """Names of the nodes that executed, in order."""

    records: tuple[ExecutionRecord, ...] = field(default=())
    """Attempt counts and outcomes for each node visit."""

    run_id: UUID | None = field(default=None, compare=False)
    """Identifier shared with execution events; None for manually constructed results."""


class WorkflowEngine(ABC):
    """Executes a :class:`Workflow` from an initial state to completion.

    Contract for implementations:

    * validate the workflow first (``workflow.validate()``);
    * follow the entry node, edges and conditional routes until ``END``;
    * raise :class:`~maximusforge.core.errors.NodeExecutionError` if a node raises, chaining
      the original exception and populating ``trace``;
    * raise :class:`~maximusforge.core.errors.WorkflowExecutionError` if ``max_steps`` is
      exceeded or a router returns an undeclared route;
    * apply node policies per attempt, exhaust eligible retries before error routing, and
      propagate external cancellation without retry or recovery;
    * return one execution record per node visit, including attempts and routed failures;
    * emit typed events to an optional per-run sink without changing execution outcomes;
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
