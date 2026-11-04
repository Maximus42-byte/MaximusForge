"""Per-run event delivery and simple replaceable sinks."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

from maximusforge.events.types import ExecutionEvent, ExecutionEventType

_logger = logging.getLogger(__name__)

_TERMINAL_EVENTS = frozenset(
    {
        ExecutionEventType.WORKFLOW_COMPLETED,
        ExecutionEventType.WORKFLOW_FAILED,
        ExecutionEventType.WORKFLOW_CANCELLED,
    }
)


class EventSink(ABC):
    """Receive execution events inline in the executing task."""

    @abstractmethod
    async def emit(self, event: ExecutionEvent) -> None: ...


class InMemoryEventCollector(EventSink):
    """Collect events for tests and small offline runs."""

    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []

    @property
    def events(self) -> tuple[ExecutionEvent, ...]:
        return tuple(self._events)

    async def emit(self, event: ExecutionEvent) -> None:
        self._events.append(event)


class LoggingEventSink(EventSink):
    """Write safe event metadata through an explicitly supplied standard logger."""

    def __init__(self, logger: logging.Logger, *, level: int = logging.INFO) -> None:
        self._logger = logger
        self._level = level

    async def emit(self, event: ExecutionEvent) -> None:
        metadata: dict[str, Any] = {
            "type": event.type.value,
            "timestamp": event.timestamp.isoformat(),
            "run_id": str(event.run_id),
            "workflow_name": event.workflow_name,
            "sequence": event.sequence,
            "node_name": event.node_name,
            "attempt": event.attempt,
            "category": event.category.value if event.category is not None else None,
            "duration_seconds": event.duration_seconds,
            "next_attempt": event.next_attempt,
            "retry_delay_seconds": event.retry_delay_seconds,
            "recovery_target": event.recovery_target,
            "recovery_for": event.recovery_for,
        }
        self._logger.log(
            self._level,
            "maximusforge.execution_event",
            extra={"maximusforge_event": metadata},
        )


class _RunEventEmitter:
    """Order run events and select at most one terminal event before delivery."""

    def __init__(self, workflow_name: str, sink: EventSink | None) -> None:
        self.workflow_name = workflow_name
        self.run_id: UUID = uuid4()
        self._sink = sink
        self._sequence = 0
        self._terminal_type: ExecutionEventType | None = None

    def _event(
        self, event_type: ExecutionEventType, **metadata: Any
    ) -> ExecutionEvent | None:
        if self._terminal_type is not None:
            return None
        event = ExecutionEvent(
            type=event_type,
            run_id=self.run_id,
            workflow_name=self.workflow_name,
            sequence=self._sequence + 1,
            **metadata,
        )
        self._sequence += 1
        if event_type in _TERMINAL_EVENTS:
            # Selection precedes the sink await: cancellation during delivery
            # cannot select another terminal event for this run.
            self._terminal_type = event_type
        return event

    async def emit(self, event_type: ExecutionEventType, **metadata: Any) -> None:
        event = self._event(event_type, **metadata)
        if event is None:
            return
        if self._sink is not None:
            try:
                await self._sink.emit(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Logging is diagnostics too; it cannot change execution results.
                with suppress(Exception):
                    _logger.warning(
                        "execution event sink failed for %s: %s",
                        event.type.value,
                        type(exc).__name__,
                    )
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError

    async def emit_cancelled(self, *, duration_seconds: float) -> None:
        event = self._event(
            ExecutionEventType.WORKFLOW_CANCELLED, duration_seconds=duration_seconds
        )
        if event is None or self._sink is None:
            return
        try:
            # A cancelled run cannot wait for diagnostics. A collector that does not
            # suspend receives the event; a slow sink is cancelled at its first await.
            async with asyncio.timeout(0):
                await self._sink.emit(event)
        except (Exception, asyncio.CancelledError):
            # Preserve the original cancellation without waiting for logging.
            pass
