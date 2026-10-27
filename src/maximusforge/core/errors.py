"""Exception hierarchy shared across MaximusForge."""

from __future__ import annotations

from collections.abc import Sequence


class MaximusForgeError(Exception):
    """Base class for all errors raised deliberately by MaximusForge."""


class ConfigurationError(MaximusForgeError):
    """Required configuration is missing or invalid."""


class InvalidStateTransitionError(MaximusForgeError):
    """An :class:`AgentState` status transition is not allowed."""


class AgentError(MaximusForgeError):
    """An agent violated its run contract."""


class LLMProviderError(MaximusForgeError):
    """An LLM provider failed to produce a response."""


class DuplicateToolError(MaximusForgeError):
    """A tool with the same name is already registered."""


class WorkflowError(MaximusForgeError):
    """Base class for workflow definition and execution errors."""


class WorkflowValidationError(WorkflowError):
    """A workflow definition is structurally invalid."""


class WorkflowExecutionError(WorkflowError):
    """A workflow failed while executing.

    ``trace`` lists the nodes that started executing, in order, up to the failure.
    """

    def __init__(self, message: str, *, trace: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.trace: tuple[str, ...] = tuple(trace)


class NodeExecutionError(WorkflowExecutionError):
    """A node raised an exception. The original exception is chained as ``__cause__``."""

    def __init__(self, node: str, message: str, *, trace: Sequence[str] = ()) -> None:
        super().__init__(f"node {node!r} failed: {message}", trace=trace)
        self.node = node
