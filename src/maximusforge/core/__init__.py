"""Shared primitives with no dependencies on other MaximusForge packages."""

from maximusforge.core.errors import (
    AgentError,
    ConfigurationError,
    DuplicateToolError,
    ExecutionOutcome,
    ExecutionRecord,
    FailureCategory,
    InvalidStateTransitionError,
    LLMProviderError,
    MaximusForgeError,
    NodeExecutionError,
    NodeFailure,
    WorkflowError,
    WorkflowExecutionError,
    WorkflowValidationError,
)
from maximusforge.core.task import Task

__all__ = [
    "AgentError",
    "ConfigurationError",
    "DuplicateToolError",
    "ExecutionOutcome",
    "ExecutionRecord",
    "FailureCategory",
    "InvalidStateTransitionError",
    "LLMProviderError",
    "MaximusForgeError",
    "NodeExecutionError",
    "NodeFailure",
    "Task",
    "WorkflowError",
    "WorkflowExecutionError",
    "WorkflowValidationError",
]
