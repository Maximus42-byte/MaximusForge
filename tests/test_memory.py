"""Contract tests for MemoryStore, run here against the in-memory reference implementation."""

from __future__ import annotations

import pytest

from maximusforge.memory import InMemoryStore, MemoryQuery, MemoryRecord, MemoryStore


@pytest.fixture
def store() -> MemoryStore:
    return InMemoryStore()


async def test_put_get_delete_roundtrip(store: MemoryStore) -> None:
    record = MemoryRecord(namespace="a", content="hello")
    await store.put(record)
    assert await store.get(record.id) == record
    assert await store.delete(record.id) is True
    assert await store.get(record.id) is None
    assert await store.delete(record.id) is False


async def test_search_is_namespaced_and_filters_text(store: MemoryStore) -> None:
    await store.put(MemoryRecord(namespace="a", content="Cats purr"))
    await store.put(MemoryRecord(namespace="a", content="dogs bark"))
    await store.put(MemoryRecord(namespace="b", content="cats elsewhere"))

    hits = await store.search(MemoryQuery(namespace="a", text="CATS"))
    assert [r.content for r in hits] == ["Cats purr"]
    assert len(await store.search(MemoryQuery(namespace="a"))) == 2


async def test_search_respects_limit_newest_first(store: MemoryStore) -> None:
    for i in range(3):
        await store.put(MemoryRecord(namespace="a", content=f"r{i}"))
    hits = await store.search(MemoryQuery(namespace="a", limit=2))
    assert [r.content for r in hits] == ["r2", "r1"]


async def test_put_overwrites_same_id(store: MemoryStore) -> None:
    record = MemoryRecord(namespace="a", content="v1")
    await store.put(record)
    await store.put(record.model_copy(update={"content": "v2"}))
    fetched = await store.get(record.id)
    assert fetched is not None
    assert fetched.content == "v2"
