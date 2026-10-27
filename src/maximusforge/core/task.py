"""The unit of work handed to an agent."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Task(BaseModel):
    """A goal an agent should accomplish, plus optional caller-supplied context."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    goal: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
