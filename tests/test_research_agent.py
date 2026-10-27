from __future__ import annotations

import pytest

from maximusforge.agents import Agent, AgentState, AgentStatus, ResearchAgent
from maximusforge.core import AgentError, Task
from maximusforge.llm.testing import ScriptedLLMProvider
from maximusforge.planning import LLMPlanner
from maximusforge.tools import ToolRegistry
from maximusforge.workflows import END, FunctionNode, Workflow, WorkflowEngine

from .conftest import FakeSearchTool


def _agent(engine: WorkflowEngine, llm: ScriptedLLMProvider) -> ResearchAgent:
    tools = ToolRegistry()
    tools.register(FakeSearchTool())
    return ResearchAgent(
        engine=engine, llm=llm, planner=LLMPlanner(llm), tools=tools, search_tool="search"
    )


async def test_research_agent_runs_end_to_end_with_fakes(engine: WorkflowEngine) -> None:
    llm = ScriptedLLMProvider(["query one\nquery two", "final answer"])
    result = await _agent(engine, llm).run(Task(goal="what is X?"))

    state = result.state
    assert state.status is AgentStatus.COMPLETED
    assert state.output == "final answer"
    assert result.trace == ("plan", "gather", "synthesize")
    assert [s.description for s in state.steps] == ["query one", "query two"]
    assert all(s.ok and s.tool_name == "search" for s in state.steps)
    # The synthesis prompt must contain the gathered evidence.
    assert "snippet about query one" in llm.calls[1][-1].content


async def test_research_agent_fails_when_no_evidence(engine: WorkflowEngine) -> None:
    llm = ScriptedLLMProvider(["boom"])  # the only planned query makes the tool raise
    result = await _agent(engine, llm).run(Task(goal="g"))

    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "no evidence could be gathered"
    assert result.trace == ("plan", "gather")  # synthesize was skipped by conditional routing
    assert len(llm.calls) == 1  # no LLM call wasted on synthesis


async def test_research_agent_continues_past_a_single_failed_step(engine: WorkflowEngine) -> None:
    llm = ScriptedLLMProvider(["boom\ngood", "answer"])
    result = await _agent(engine, llm).run(Task(goal="g"))
    assert result.state.status is AgentStatus.COMPLETED
    assert [s.ok for s in result.state.steps] == [False, True]


async def test_agent_run_rejects_workflow_that_never_reaches_terminal_status(
    engine: WorkflowEngine,
) -> None:
    class Lazy(Agent):
        def build_workflow(self) -> Workflow[AgentState]:
            async def passthrough(state: AgentState) -> AgentState:
                return state

            return (
                Workflow("lazy", AgentState)
                .add_node(FunctionNode("n", passthrough))
                .set_entry("n")
                .add_edge("n", END)
            )

    with pytest.raises(AgentError):
        await Lazy("lazy", engine).run(Task(goal="g"))
