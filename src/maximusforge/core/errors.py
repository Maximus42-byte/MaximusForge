"""Exception hierarchy and privacy-safe execution metadata shared across MaximusForge."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


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


class FailureCategory(StrEnum):
    """Machine-readable category of an execution failure."""

    NODE_ERROR = "node_error"
    CONFIGURATION = "configuration"
    VALIDATION = "validation"
    TIMEOUT = "timeout"
    ROUTING = "routing"
    STEP_LIMIT = "step_limit"
    INVALID_STATE = "invalid_state"


class ExecutionOutcome(StrEnum):
    """Outcome of one node visit, excluding internal retry attempts."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROUTED = "routed"


@dataclass(frozen=True, slots=True)
class NodeFailure:
    """Failure context provided to a recovery node; excludes raw inputs and messages."""

    node: str
    attempts: int
    category: FailureCategory
    exception_type: str
    timeouts: int = 0


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """Structured metadata for one node visit in execution order."""

    node: str
    attempts: int
    outcome: ExecutionOutcome
    timeouts: int = 0
    failure: NodeFailure | None = None
    routed_to: str | None = None
    last_error_category: FailureCategory | None = None
    last_exception_type: str | None = None

    @property
    def retries(self) -> int:
        """Number of attempts after the initial call."""
        return self.attempts - 1


class WorkflowExecutionError(WorkflowError):
    """A workflow failed; ``trace`` and ``records`` describe work already attempted."""

    def __init__(
        self,
        message: str,
        *,
        trace: Sequence[str] = (),
        records: Sequence[ExecutionRecord] = (),
        category: FailureCategory = FailureCategory.NODE_ERROR,
    ) -> None:
        super().__init__(message)
        self.trace: tuple[str, ...] = tuple(trace)
        self.records: tuple[ExecutionRecord, ...] = tuple(records)
        self.category = category


class NodeExecutionError(WorkflowExecutionError):
    """A node failed after retries; the final exception is chained as ``__cause__``."""

    def __init__(
        self,
        node: str,
        message: str,
        *,
        trace: Sequence[str] = (),
        records: Sequence[ExecutionRecord] = (),
        failure: NodeFailure | None = None,
    ) -> None:
        super().__init__(
            f"node {node!r} failed: {message}",
            trace=trace,
            records=records,
            category=failure.category if failure is not None else FailureCategory.NODE_ERROR,
        )
        self.node = node
        self.failure = failure
