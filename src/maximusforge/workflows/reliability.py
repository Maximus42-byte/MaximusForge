"""Engine-neutral node policies and cooperative async attempt execution."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import ValidationError

from maximusforge.core.errors import (
    ConfigurationError,
    FailureCategory,
    InvalidStateTransitionError,
    WorkflowExecutionError,
    WorkflowValidationError,
)

T = TypeVar("T")
_NON_RETRYABLE = (
    ConfigurationError,
    ValidationError,
    InvalidStateTransitionError,
    WorkflowExecutionError,
    WorkflowValidationError,
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Explicit opt-in to retry named exception classes or per-attempt timeouts.

    ``max_attempts`` includes the initial call. A retry can repeat external side effects;
    configure this only for operations whose repeated execution is safe.
    """

    max_attempts: int
    retry_on: tuple[type[Exception], ...] = ()
    retry_timeouts: bool = False
    initial_delay: float = 0.5
    backoff_multiplier: float = 2.0
    max_delay: float = 5.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if type(self.retry_timeouts) is not bool:
            raise ValueError("retry_timeouts must be a boolean")
        if self.max_attempts > 1 and not (self.retry_on or self.retry_timeouts):
            raise ValueError("retries require retry_on or retry_timeouts")
        if not isinstance(self.retry_on, tuple) or any(
            not isinstance(exc_type, type)
            or not issubclass(exc_type, Exception)
            or exc_type is Exception
            for exc_type in self.retry_on
        ):
            raise ValueError("retry_on must contain specific Exception classes")
        for label, value, minimum in (
            ("initial_delay", self.initial_delay, 0.0),
            ("max_delay", self.max_delay, 0.0),
            ("backoff_multiplier", self.backoff_multiplier, 1.0),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < minimum
            ):
                raise ValueError(f"{label} must be finite and >= {minimum}")

    def delay_after(self, failed_attempt: int) -> float:
        """Delay before the next attempt, capped at ``max_delay``."""
        if type(failed_attempt) is not int or failed_attempt < 1:
            raise ValueError("failed_attempt must be >= 1")
        delay = min(self.initial_delay, self.max_delay)
        for _ in range(failed_attempt - 1):
            delay = min(delay * self.backoff_multiplier, self.max_delay)
            if delay == self.max_delay:
                break
        return delay


@dataclass(frozen=True, slots=True)
class NodeExecutionPolicy:
    """Optional per-attempt timeout and retry policy for a workflow node."""

    timeout_seconds: float | None = None
    retry_policy: RetryPolicy | None = None

    def __post_init__(self) -> None:
        timeout = self.timeout_seconds
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if self.retry_policy is not None and not isinstance(self.retry_policy, RetryPolicy):
            raise ValueError("retry_policy must be a RetryPolicy")


@dataclass(frozen=True, slots=True)
class _AttemptResult(Generic[T]):
    value: T | None
    error: Exception | None
    attempts: int
    timeouts: int
    category: FailureCategory | None
    last_error_category: FailureCategory | None
    last_exception_type: str | None


class _AttemptObserver(Protocol):
    """Engine-neutral attempt transitions, supplied by an engine when events are enabled."""

    async def started(self, attempt: int) -> None: ...

    async def failed(
        self, attempt: int, category: FailureCategory, duration_seconds: float
    ) -> None: ...

    async def retry_scheduled(
        self, attempt: int, next_attempt: int, category: FailureCategory, delay_seconds: float
    ) -> None: ...


def _is_recoverable(error: Exception) -> bool:
    return not isinstance(error, (*_NON_RETRYABLE, WorkflowExecutionError))


def _category(error: Exception, timed_out: bool) -> FailureCategory:
    if timed_out:
        return FailureCategory.TIMEOUT
    if isinstance(error, WorkflowExecutionError):
        return error.category
    if isinstance(error, ConfigurationError):
        return FailureCategory.CONFIGURATION
    if isinstance(error, (ValidationError, InvalidStateTransitionError, WorkflowValidationError)):
        return FailureCategory.VALIDATION
    return FailureCategory.NODE_ERROR


async def _sleep_backoff(delay: float) -> None:
    """Wait between attempts; cancellation interrupts this await."""
    await asyncio.sleep(delay)


async def _run_attempts(
    run: Callable[[], Awaitable[T]],
    policy: NodeExecutionPolicy | None,
    *,
    observer: _AttemptObserver | None = None,
) -> _AttemptResult[T]:
    """Run one node invocation; cancellation remains a BaseException and propagates."""
    policy = policy or NodeExecutionPolicy()
    retry = policy.retry_policy
    limit = retry.max_attempts if retry is not None else 1
    timeouts = 0
    last_error_category: FailureCategory | None = None
    last_exception_type: str | None = None
    for attempt in range(1, limit + 1):
        if observer is not None:
            await observer.started(attempt)
        started_at = time.perf_counter()
        deadline = asyncio.timeout(policy.timeout_seconds)
        try:
            async with deadline:
                value = await run()
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            if deadline.expired():
                raise TimeoutError("node attempt exceeded its timeout")
        except Exception as exc:
            timed_out = deadline.expired()
            if timed_out:
                timeouts += 1
            category = _category(exc, timed_out)
            if observer is not None:
                await observer.failed(attempt, category, time.perf_counter() - started_at)
            last_error_category = category
            last_exception_type = type(exc).__name__
            eligible = (
                retry is not None
                and attempt < limit
                and _is_recoverable(exc)
                and (
                    (timed_out and retry.retry_timeouts)
                    or (not timed_out and isinstance(exc, retry.retry_on))
                )
            )
            if not eligible:
                return _AttemptResult(
                    None,
                    exc,
                    attempt,
                    timeouts,
                    category,
                    last_error_category,
                    last_exception_type,
                )
            assert retry is not None
            delay = retry.delay_after(attempt)
            if observer is not None:
                await observer.retry_scheduled(attempt, attempt + 1, category, delay)
            await _sleep_backoff(delay)
        else:
            return _AttemptResult(
                value,
                None,
                attempt,
                timeouts,
                None,
                last_error_category,
                last_exception_type,
            )
    raise AssertionError("attempt loop ended without a result")
