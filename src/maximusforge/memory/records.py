"""Backend-neutral memory data types."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class MemoryRecord(BaseModel):
    """One persisted unit of memory. ``namespace`` scopes records (per agent, per user, ...)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    namespace: str = Field(min_length=1)
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryQuery(BaseModel):
    """Lexical/metadata query. ``text=None`` matches every record in the namespace."""

    model_config = ConfigDict(frozen=True)

    namespace: str = Field(min_length=1)
    text: str | None = None
    limit: int = Field(default=10, ge=1)


class ScoredMemory(BaseModel):
    """A record with a similarity score (higher is more similar)."""

    model_config = ConfigDict(frozen=True)

    record: MemoryRecord
    score: float
