"""Process-local MemoryStore for tests and local experiments.

NOT durable and NOT semantic: data is lost on exit and ``search`` is a case-insensitive
substring match. It exists to exercise the interface; it is not a production backend.
"""

from __future__ import annotations

from maximusforge.memory.records import MemoryQuery, MemoryRecord
from maximusforge.memory.store import MemoryStore


class InMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    async def put(self, record: MemoryRecord) -> None:
        self._records[record.id] = record

    async def get(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    async def search(self, query: MemoryQuery) -> list[MemoryRecord]:
        needle = query.text.lower() if query.text is not None else None
        # Reversed insertion order + stable sort => newest first even if timestamps tie.
        matches = [
            r
            for r in reversed(list(self._records.values()))
            if r.namespace == query.namespace and (needle is None or needle in r.content.lower())
        ]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[: query.limit]

    async def delete(self, record_id: str) -> bool:
        return self._records.pop(record_id, None) is not None
