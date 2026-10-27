"""Graph workflow definitions and the engine interface (ADR-002, ADR-006)."""

from maximusforge.workflows.engine import ExecutionConfig, WorkflowEngine, WorkflowResult
from maximusforge.workflows.workflow import (
    END,
    ConditionalEdge,
    Edge,
    FunctionNode,
    Workflow,
    WorkflowNode,
)

__all__ = [
    "END",
    "ConditionalEdge",
    "Edge",
    "ExecutionConfig",
    "FunctionNode",
    "Workflow",
    "WorkflowEngine",
    "WorkflowNode",
    "WorkflowResult",
]
