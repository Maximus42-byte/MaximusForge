"""LangGraph-backed :class:`WorkflowEngine`.

This is the ONLY module allowed to import ``langgraph`` (enforced by a Ruff rule and
``tests/test_architecture.py``). Swapping in the native MaximusForge graph engine means adding a
sibling module that implements ``WorkflowEngine``; no agent code changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.errors import GraphRecursionError
from langgraph.graph import END as LG_END
from langgraph.graph import StateGraph
from pydantic import BaseModel

from maximusforge.core.errors import NodeExecutionError, WorkflowExecutionError
from maximusforge.workflows.engine import ExecutionConfig, WorkflowEngine, WorkflowResult
from maximusforge.workflows.workflow import END, S, Workflow


class LangGraphEngine(WorkflowEngine):
    """Compiles a :class:`Workflow` to a LangGraph ``StateGraph`` per execution.

    Compiling per call keeps the engine stateless; compilation cost is negligible next to
    node execution.
    """

    async def execute(
        self,
        workflow: Workflow[S],
        initial_state: S,
        *,
        config: ExecutionConfig | None = None,
    ) -> WorkflowResult[S]:
        workflow.validate()
        config = config or ExecutionConfig()
        if not isinstance(initial_state, workflow.state_type):
            raise WorkflowExecutionError("initial state has the wrong type")
        trace: list[str] = []
        compiled = self._compile(workflow, trace, config.max_steps)

        try:
            output = await compiled.ainvoke(
                initial_state, config={"recursion_limit": config.max_steps + 1}
            )
        except GraphRecursionError as exc:
            raise WorkflowExecutionError(
                f"workflow {workflow.name!r} exceeded max_steps={config.max_steps}", trace=trace
            ) from exc

        return WorkflowResult(state=_coerce(workflow.state_type, output), trace=tuple(trace))

    def _compile(self, workflow: Workflow[S], trace: list[str], max_steps: int) -> Any:
        graph = StateGraph(workflow.state_type)

        for name, node in workflow.nodes.items():
            graph.add_node(
                name,
                RunnableLambda(
                    _wrap_node(workflow.state_type, name, node.run, trace, max_steps)
                ),
            )

        graph.set_entry_point(workflow.entry)
        for edge in workflow.edges.values():
            graph.add_edge(edge.source, _target(edge.target))
        for cond in workflow.conditional_edges.values():
            graph.add_conditional_edges(
                cond.source,
                _wrap_router(workflow.state_type, cond.source, cond.router, cond.routes, trace),
                {key: _target(target) for key, target in cond.routes.items()},
            )
        return graph.compile()


def _target(name: str) -> str:
    return LG_END if name == END else name


def _coerce(state_type: type[S], value: object) -> S:
    """LangGraph hands back model instances or plain dicts depending on version/config."""
    if isinstance(value, state_type):
        return value
    return state_type.model_validate(value)


def _as_update(state: BaseModel) -> dict[str, Any]:
    # Shallow field dict (not model_dump) so nested models stay model instances.
    return {name: getattr(state, name) for name in type(state).model_fields}


def _wrap_node(
    state_type: type[S],
    name: str,
    run: Callable[[S], Awaitable[S]],
    trace: list[str],
    max_steps: int,
) -> Callable[[BaseModel], Awaitable[dict[str, Any]]]:
    async def node(state: BaseModel) -> dict[str, Any]:
        if len(trace) >= max_steps:
            raise WorkflowExecutionError(f"workflow exceeded max_steps={max_steps}", trace=trace)
        trace.append(name)
        try:
            new_state = await run(_coerce(state_type, state))
            if not isinstance(new_state, state_type):
                raise TypeError(f"node {name!r} returned the wrong state type")
        except Exception as exc:
            raise NodeExecutionError(name, type(exc).__name__, trace=trace) from exc
        return _as_update(new_state)

    return node


def _wrap_router(
    state_type: type[S],
    source: str,
    router: Callable[[S], str],
    routes: Mapping[str, str],
    trace: list[str],
) -> Callable[[S], str]:
    def route(state: S) -> str:
        try:
            key = router(_coerce(state_type, state))
        except Exception as exc:
            raise WorkflowExecutionError(
                f"router after {source!r} raised {type(exc).__name__}", trace=trace
            ) from exc
        if key not in routes:
            raise WorkflowExecutionError(
                f"router after {source!r} returned undeclared route {key!r}", trace=trace
            )
        return key

    return route
