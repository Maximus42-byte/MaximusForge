"""Memory interfaces. Agents depend on these, never on a database (ADR-003)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from maximusforge.memory.records import MemoryQuery, MemoryRecord, ScoredMemory


class MemoryStore(ABC):
    """Persistent record storage with simple retrieval.

    Working (short-term) memory is not a store: it is the workflow state carried through a
    run (see ``AgentState``). Stores hold what outlives a run.
    """

    @abstractmethod
    async def put(self, record: MemoryRecord) -> None:
        """Insert or overwrite the record with the same ``id``."""

    @abstractmethod
    async def get(self, record_id: str) -> MemoryRecord | None: ...

    @abstractmethod
    async def search(self, query: MemoryQuery) -> list[MemoryRecord]:
        """Return matching records, newest first, at most ``query.limit``."""

    @abstractmethod
    async def delete(self, record_id: str) -> bool:
        """Remove a record. Returns whether it existed."""


class SemanticMemoryStore(MemoryStore):
    """A store that can also retrieve by meaning.

    Embedding is the implementation's concern (a pgvector-backed store will own or be given an
    embedder); callers pass plain text.
    """

    @abstractmethod
    async def search_similar(
        self, text: str, *, namespace: str, limit: int = 10
    ) -> list[ScoredMemory]:
        """Return the records most similar to ``text``, best first."""
