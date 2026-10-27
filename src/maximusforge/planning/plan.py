"""Operational plan representation: what will be done, not why the model thinks so."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str = Field(min_length=1)


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True)

    goal: str
    steps: tuple[PlanStep, ...] = ()
