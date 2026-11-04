"""Immutable, backend-neutral workflow execution events."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from maximusforge.core.errors import FailureCategory


class ExecutionEventType(StrEnum):
    WORKFLOW_STARTED = "workflow_started"
    NODE_STARTED = "node_started"
    NODE_ATTEMPT_FAILED = "node_attempt_failed"
    NODE_RETRY_SCHEDULED = "node_retry_scheduled"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_RECOVERY_ROUTED = "node_recovery_routed"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_CANCELLED = "workflow_cancelled"


_NODE_EVENTS = frozenset(
    {
        ExecutionEventType.NODE_STARTED,
        ExecutionEventType.NODE_ATTEMPT_FAILED,
        ExecutionEventType.NODE_RETRY_SCHEDULED,
        ExecutionEventType.NODE_COMPLETED,
        ExecutionEventType.NODE_FAILED,
        ExecutionEventType.NODE_RECOVERY_ROUTED,
    }
)
_CATEGORIZED_EVENTS = frozenset(
    {
        ExecutionEventType.NODE_ATTEMPT_FAILED,
        ExecutionEventType.NODE_RETRY_SCHEDULED,
        ExecutionEventType.NODE_FAILED,
        ExecutionEventType.NODE_RECOVERY_ROUTED,
        ExecutionEventType.WORKFLOW_FAILED,
    }
)
_TIMED_EVENTS = frozenset(
    {
        ExecutionEventType.NODE_ATTEMPT_FAILED,
        ExecutionEventType.NODE_COMPLETED,
        ExecutionEventType.NODE_FAILED,
        ExecutionEventType.WORKFLOW_COMPLETED,
        ExecutionEventType.WORKFLOW_FAILED,
        ExecutionEventType.WORKFLOW_CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """A privacy-safe execution transition, ordered by ``sequence`` within a run.

    Only metadata belongs here: no workflow state, exception text, prompts, or tool arguments.
    The event type conveys the transition's status or outcome.
    """

    type: ExecutionEventType
    run_id: UUID
    workflow_name: str
    sequence: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    node_name: str | None = None
    attempt: int | None = None
    category: FailureCategory | None = None
    duration_seconds: float | None = None
    next_attempt: int | None = None
    retry_delay_seconds: float | None = None
    recovery_target: str | None = None
    recovery_for: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.type, ExecutionEventType):
            raise ValueError("type must be an ExecutionEventType")
        if not isinstance(self.run_id, UUID):
            raise ValueError("run_id must be a UUID")
        if not isinstance(self.workflow_name, str):
            raise ValueError("workflow_name must be a string")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        if not isinstance(self.timestamp, datetime) or self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("timestamp must have a UTC offset of zero")
        for label, integer_value in (
            ("attempt", self.attempt),
            ("next_attempt", self.next_attempt),
        ):
            if integer_value is not None and (type(integer_value) is not int or integer_value < 1):
                raise ValueError(f"{label} must be a positive integer")
        for label, number_value in (
            ("duration_seconds", self.duration_seconds),
            ("retry_delay_seconds", self.retry_delay_seconds),
        ):
            if number_value is not None and (
                isinstance(number_value, bool)
                or not isinstance(number_value, (int, float))
                or not math.isfinite(number_value)
                or number_value < 0
            ):
                raise ValueError(f"{label} must be finite and nonnegative")
        if self.category is not None and not isinstance(self.category, FailureCategory):
            raise ValueError("category must be a FailureCategory")
        for label, text_value in (
            ("node_name", self.node_name),
            ("recovery_target", self.recovery_target),
            ("recovery_for", self.recovery_for),
        ):
            if text_value is not None and (not isinstance(text_value, str) or not text_value):
                raise ValueError(f"{label} must be a nonempty string")
        is_node = self.type in _NODE_EVENTS
        if is_node:
            if self.node_name is None or self.attempt is None:
                raise ValueError("node events require node_name and attempt")
        elif self.node_name is not None or self.attempt is not None:
            raise ValueError("workflow events cannot carry node_name or attempt")
        if (self.type in _CATEGORIZED_EVENTS) != (self.category is not None):
            raise ValueError("category is required only for failure and retry events")
        if (self.type in _TIMED_EVENTS) != (self.duration_seconds is not None):
            raise ValueError("duration_seconds is required only for timed events")
        is_retry = self.type is ExecutionEventType.NODE_RETRY_SCHEDULED
        if is_retry:
            assert self.attempt is not None
            if self.next_attempt != self.attempt + 1 or self.retry_delay_seconds is None:
                raise ValueError("retry event needs the next attempt and delay")
        elif self.next_attempt is not None or self.retry_delay_seconds is not None:
            raise ValueError("retry metadata belongs only to retry events")
        if (self.type is ExecutionEventType.NODE_RECOVERY_ROUTED) != (
            self.recovery_target is not None
        ):
            raise ValueError("recovery_target belongs only to recovery routing")
        if self.recovery_for is not None and not is_node:
            raise ValueError("recovery_for belongs only to node events")
