from __future__ import annotations

import pytest

from maximusforge.core import Task
from maximusforge.llm.testing import ScriptedLLMProvider
from maximusforge.planning import LLMPlanner


async def test_llm_planner_parses_one_step_per_line_and_strips_markers() -> None:
    llm = ScriptedLLMProvider(["1. find history\n- find uses\n\n* compare\n"])
    plan = await LLMPlanner(llm).plan(Task(goal="learn about X"))
    assert plan.goal == "learn about X"
    assert [s.description for s in plan.steps] == ["find history", "find uses", "compare"]


async def test_llm_planner_caps_steps() -> None:
    llm = ScriptedLLMProvider(["a\nb\nc\nd"])
    plan = await LLMPlanner(llm, max_steps=2).plan(Task(goal="g"))
    assert [s.description for s in plan.steps] == ["a", "b"]


async def test_llm_planner_sends_goal_to_model() -> None:
    llm = ScriptedLLMProvider(["a"])
    await LLMPlanner(llm).plan(Task(goal="my goal"))
    assert llm.calls[0][-1].content == "my goal"


def test_planner_rejects_nonpositive_max_steps() -> None:
    with pytest.raises(ValueError):
        LLMPlanner(ScriptedLLMProvider([]), max_steps=0)
