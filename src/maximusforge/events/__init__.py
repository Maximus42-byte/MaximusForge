"""Backend-neutral execution events and sinks."""

from maximusforge.events.sink import EventSink, InMemoryEventCollector, LoggingEventSink
from maximusforge.events.types import ExecutionEvent, ExecutionEventType

__all__ = [
    "EventSink",
    "ExecutionEvent",
    "ExecutionEventType",
    "InMemoryEventCollector",
    "LoggingEventSink",
]
