"""Engine-neutral workflow definition: nodes, edges, and conditional routing.

A :class:`Workflow` is plain data plus callables. It knows nothing about how it will be run;
a :class:`~maximusforge.workflows.engine.WorkflowEngine` interprets it (ADR-002, ADR-006).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Self, TypeVar

from pydantic import BaseModel

from maximusforge.core.errors import NodeFailure, WorkflowValidationError
from maximusforge.workflows.reliability import NodeExecutionPolicy

END = "__end__"
"""Sentinel target meaning "the workflow is finished"."""

S = TypeVar("S", bound=BaseModel)

Router = Callable[[S], str]
"""Chooses a route key from the current state. Must be a pure function of the state."""


class WorkflowNode(ABC, Generic[S]):
    """A unit of work: receives the current state and returns the next state.

    States should be treated as immutable values: return a new state rather than mutating.
    """

    def __init__(self, name: str, *, policy: NodeExecutionPolicy | None = None) -> None:
        if not name or name in {"__start__", END}:
            raise ValueError(f"invalid node name {name!r}")
        self.name = name
        self.policy = policy

    @abstractmethod
    async def run(self, state: S) -> S: ...


class FunctionNode(WorkflowNode[S]):
    """Adapts an async function to a node."""

    def __init__(
        self,
        name: str,
        fn: Callable[[S], Awaitable[S]],
        *,
        policy: NodeExecutionPolicy | None = None,
    ) -> None:
        super().__init__(name, policy=policy)
        self._fn = fn

    async def run(self, state: S) -> S:
        return await self._fn(state)


class RecoveryNode(WorkflowNode[S]):
    """A node entered only through an error edge, with structured failure context."""

    async def run(self, state: S) -> S:
        raise RuntimeError("recovery nodes require a failure context")

    @abstractmethod
    async def recover(self, state: S, failure: NodeFailure) -> S: ...


class FunctionRecoveryNode(RecoveryNode[S]):
    """Adapt an async ``(state, failure)`` function to a recovery node."""

    def __init__(
        self,
        name: str,
        fn: Callable[[S, NodeFailure], Awaitable[S]],
        *,
        policy: NodeExecutionPolicy | None = None,
    ) -> None:
        super().__init__(name, policy=policy)
        self._fn = fn

    async def recover(self, state: S, failure: NodeFailure) -> S:
        return await self._fn(state, failure)


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class ErrorEdge:
    """Route exhausted, eligible node failures to a recovery node."""

    source: str
    target: str


@dataclass(frozen=True, slots=True)
class ConditionalEdge(Generic[S]):
    source: str
    router: Router[S]
    routes: Mapping[str, str]  # route key -> node name or END


class Workflow(Generic[S]):
    """A directed graph of nodes over a state type.

    Execution is sequential. Each node has one normal outgoing connection and may also
    have one error edge to a recovery node. Fan-out and parallelism remain unsupported.
    """

    def __init__(self, name: str, state_type: type[S]) -> None:
        self.name = name
        self.state_type = state_type
        self._nodes: dict[str, WorkflowNode[S]] = {}
        self._edges: dict[str, Edge] = {}
        self._conditional: dict[str, ConditionalEdge[S]] = {}
        self._error_edges: dict[str, ErrorEdge] = {}
        self._entry: str | None = None

    # -- definition -----------------------------------------------------------------------

    def add_node(self, node: WorkflowNode[S]) -> Self:
        if node.name in self._nodes:
            raise WorkflowValidationError(f"duplicate node {node.name!r}")
        self._nodes[node.name] = node
        return self

    def set_entry(self, name: str) -> Self:
        self._entry = name
        return self

    def add_edge(self, source: str, target: str) -> Self:
        self._claim_source(source)
        self._edges[source] = Edge(source, target)
        return self

    def add_conditional_edges(
        self, source: str, router: Router[S], routes: Mapping[str, str]
    ) -> Self:
        self._claim_source(source)
        self._conditional[source] = ConditionalEdge(source, router, dict(routes))
        return self

    def add_error_edge(self, source: str, recovery: str) -> Self:
        """Route eligible failures from ``source`` to a :class:`RecoveryNode`."""
        if source in self._error_edges:
            raise WorkflowValidationError(f"node {source!r} already has an error connection")
        self._error_edges[source] = ErrorEdge(source, recovery)
        return self

    def _claim_source(self, source: str) -> None:
        if source in self._edges or source in self._conditional:
            raise WorkflowValidationError(f"node {source!r} already has an outgoing connection")

    # -- read access (for engines) ------------------------------------------------------

    @property
    def entry(self) -> str:
        if self._entry is None:
            raise WorkflowValidationError("workflow has no entry node")
        return self._entry

    @property
    def nodes(self) -> Mapping[str, WorkflowNode[S]]:
        return MappingProxyType(self._nodes)

    @property
    def edges(self) -> Mapping[str, Edge]:
        return MappingProxyType(self._edges)

    @property
    def conditional_edges(self) -> Mapping[str, ConditionalEdge[S]]:
        return MappingProxyType(self._conditional)

    @property
    def error_edges(self) -> Mapping[str, ErrorEdge]:
        return MappingProxyType(self._error_edges)

    # -- validation -----------------------------------------------------------------------

    def validate(self) -> None:
        """Raise :class:`WorkflowValidationError` if the definition cannot be executed."""
        if not self._nodes:
            raise WorkflowValidationError("workflow has no nodes")
        if self.entry not in self._nodes:
            raise WorkflowValidationError(f"entry node {self.entry!r} is not defined")
        if isinstance(self._nodes[self.entry], RecoveryNode):
            raise WorkflowValidationError("a recovery node cannot be the entry node")

        def check_target(origin: str, target: str) -> None:
            if target != END and target not in self._nodes:
                raise WorkflowValidationError(f"{origin!r} points to undefined node {target!r}")

        for source in (*self._edges, *self._conditional, *self._error_edges):
            if source not in self._nodes:
                raise WorkflowValidationError(f"connection from undefined node {source!r}")
        for edge in self._edges.values():
            check_target(edge.source, edge.target)
            if edge.target != END and isinstance(self._nodes[edge.target], RecoveryNode):
                raise WorkflowValidationError("normal edges cannot target recovery nodes")
        for cond in self._conditional.values():
            if not cond.routes:
                raise WorkflowValidationError(f"conditional edge {cond.source!r} has no routes")
            for target in cond.routes.values():
                check_target(cond.source, target)
                if target != END and isinstance(self._nodes[target], RecoveryNode):
                    raise WorkflowValidationError("normal routes cannot target recovery nodes")
        for error_edge in self._error_edges.values():
            check_target(error_edge.source, error_edge.target)
            if error_edge.target == END or not isinstance(
                self._nodes[error_edge.target], RecoveryNode
            ):
                raise WorkflowValidationError("error edges must target recovery nodes")
        for name in self._nodes:
            if name not in self._edges and name not in self._conditional:
                raise WorkflowValidationError(f"node {name!r} has no outgoing connection")
