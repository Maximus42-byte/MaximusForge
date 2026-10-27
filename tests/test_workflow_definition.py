from __future__ import annotations

import pytest
from pydantic import BaseModel

from maximusforge.core import WorkflowValidationError
from maximusforge.workflows import END, FunctionNode, Workflow


class Counter(BaseModel):
    n: int = 0


async def _noop(state: Counter) -> Counter:
    return state


def _node(name: str) -> FunctionNode[Counter]:
    return FunctionNode(name, _noop)


def test_valid_workflow_passes_validation() -> None:
    wf = Workflow("w", Counter).add_node(_node("a")).set_entry("a").add_edge("a", END)
    wf.validate()


def test_duplicate_node_rejected() -> None:
    with pytest.raises(WorkflowValidationError):
        Workflow("w", Counter).add_node(_node("a")).add_node(_node("a"))


def test_second_outgoing_connection_rejected() -> None:
    wf = Workflow("w", Counter).add_node(_node("a")).add_edge("a", END)
    with pytest.raises(WorkflowValidationError):
        wf.add_edge("a", END)
    with pytest.raises(WorkflowValidationError):
        wf.add_conditional_edges("a", lambda s: "x", {"x": END})


@pytest.mark.parametrize("name", ["", "__start__", END])
def test_invalid_node_names_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="invalid node name"):
        _node(name)


def test_missing_entry_rejected() -> None:
    wf = Workflow("w", Counter).add_node(_node("a")).add_edge("a", END)
    with pytest.raises(WorkflowValidationError, match="no entry"):
        wf.validate()


def test_undefined_entry_rejected() -> None:
    wf = Workflow("w", Counter).add_node(_node("a")).set_entry("zzz").add_edge("a", END)
    with pytest.raises(WorkflowValidationError, match="not defined"):
        wf.validate()


def test_edge_to_undefined_node_rejected() -> None:
    wf = Workflow("w", Counter).add_node(_node("a")).set_entry("a").add_edge("a", "ghost")
    with pytest.raises(WorkflowValidationError, match="undefined node"):
        wf.validate()


def test_conditional_route_to_undefined_node_rejected() -> None:
    wf = (
        Workflow("w", Counter)
        .add_node(_node("a"))
        .set_entry("a")
        .add_conditional_edges("a", lambda s: "x", {"x": "ghost"})
    )
    with pytest.raises(WorkflowValidationError, match="undefined node"):
        wf.validate()


def test_node_without_outgoing_connection_rejected() -> None:
    wf = Workflow("w", Counter).add_node(_node("a")).set_entry("a")
    with pytest.raises(WorkflowValidationError, match="no outgoing"):
        wf.validate()


def test_empty_workflow_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="no nodes"):
        Workflow("w", Counter).validate()
