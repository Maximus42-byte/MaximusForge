"""Planners turn a task into a :class:`Plan`."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from maximusforge.core.task import Task
from maximusforge.llm.provider import LLMProvider
from maximusforge.llm.types import Message
from maximusforge.planning.plan import Plan, PlanStep

_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


class Planner(ABC):
    @abstractmethod
    async def plan(self, task: Task) -> Plan: ...


class LLMPlanner(Planner):
    """Asks an LLM for one short step per line and parses the reply into a plan.

    Only the resulting steps are kept; any other text the model produces is discarded.
    """

    def __init__(self, llm: LLMProvider, *, max_steps: int = 5) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self._llm = llm
        self._max_steps = max_steps

    async def plan(self, task: Task) -> Plan:
        response = await self._llm.generate(
            [
                Message.system(
                    f"Break the user's goal into at most {self._max_steps} short, concrete "
                    "steps. Reply with exactly one step per line and nothing else."
                ),
                Message.user(task.goal),
            ]
        )
        lines = (_LIST_MARKER.sub("", line).strip() for line in response.content.splitlines())
        steps = tuple(PlanStep(description=line) for line in lines if line)
        return Plan(goal=task.goal, steps=steps[: self._max_steps])
