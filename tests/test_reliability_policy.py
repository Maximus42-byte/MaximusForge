"""Policy value and backoff tests independent of a graph backend."""

from __future__ import annotations

import math
from typing import cast

import pytest

from maximusforge.workflows import NodeExecutionPolicy, RetryPolicy


class TransientError(Exception):
    pass


@pytest.mark.parametrize("attempts", [0, -1, 1.5, True])
def test_invalid_attempt_count(attempts: int) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=attempts)


def test_retry_needs_explicit_eligibility() -> None:
    with pytest.raises(ValueError, match="retry_on or retry_timeouts"):
        RetryPolicy(max_attempts=2)
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=2, retry_on=(Exception,))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_delay", -1.0),
        ("max_delay", float("inf")),
        ("backoff_multiplier", 0.5),
        ("backoff_multiplier", float("nan")),
    ],
)
def test_invalid_backoff_values(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        if field == "initial_delay":
            RetryPolicy(max_attempts=2, retry_on=(TransientError,), initial_delay=value)
        elif field == "max_delay":
            RetryPolicy(max_attempts=2, retry_on=(TransientError,), max_delay=value)
        else:
            RetryPolicy(max_attempts=2, retry_on=(TransientError,), backoff_multiplier=value)


def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(
        max_attempts=6,
        retry_on=(TransientError,),
        initial_delay=0.5,
        backoff_multiplier=3,
        max_delay=2,
    )
    assert [policy.delay_after(i) for i in range(1, 6)] == [0.5, 1.5, 2, 2, 2]
    assert math.isfinite(policy.delay_after(1000))


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout(timeout: float) -> None:
    with pytest.raises(ValueError):
        NodeExecutionPolicy(timeout_seconds=timeout)


def test_invalid_policy_types_are_rejected() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=2, retry_on=(TransientError,), retry_timeouts=cast(bool, 1))
    with pytest.raises(ValueError):
        NodeExecutionPolicy(timeout_seconds=True)
    with pytest.raises(ValueError):
        NodeExecutionPolicy(retry_policy=cast(RetryPolicy, "not a policy"))
