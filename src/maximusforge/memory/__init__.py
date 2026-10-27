"""Memory abstractions (ADR-003). No database backend exists yet."""

from maximusforge.memory.in_memory import InMemoryStore
from maximusforge.memory.records import MemoryQuery, MemoryRecord, ScoredMemory
from maximusforge.memory.store import MemoryStore, SemanticMemoryStore

__all__ = [
    "InMemoryStore",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryStore",
    "ScoredMemory",
    "SemanticMemoryStore",
]
