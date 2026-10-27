"""ResearchAgent: a deliberately minimal agent that proves the architecture end to end.

    plan --> gather --(any evidence?)--> synthesize --> END
                     \\--(none)---------> END (failed)

* ``plan``       a Planner turns the task into search queries.
* ``gather``     each query is run through a named tool from the ToolRegistry.
* ``synthesize`` the LLM answers using only the gathered observations.

It ships no search tool: callers inject a registry containing a tool that accepts a ``query``
string argument. It is a skeleton for the future Research Assistant, not that product.
"""

from __future__ import annotations

from maximusforge.agents.base import Agent
from maximusforge.agents.state import AgentState, AgentStatus, ExecutionStep
from maximusforge.llm.provider import LLMProvider
from maximusforge.llm.types import Message
from maximusforge.planning.planner import Planner
from maximusforge.tools.registry import ToolRegistry
from maximusforge.workflows.engine import WorkflowEngine
from maximusforge.workflows.workflow import END, Workflow, WorkflowNode


class _PlanNode(WorkflowNode[AgentState]):
    def __init__(self, planner: Planner) -> None:
        super().__init__("plan")
        self._planner = planner

    async def run(self, state: AgentState) -> AgentState:
        return state.with_plan(await self._planner.plan(state.task))


class _GatherNode(WorkflowNode[AgentState]):
    def __init__(self, tools: ToolRegistry, search_tool: str) -> None:
        super().__init__("gather")
        self._tools = tools
        self._search_tool = search_tool

    async def run(self, state: AgentState) -> AgentState:
        steps = state.plan.steps if state.plan else ()
        for step in steps:
            arguments = {"query": step.description}
            result = await self._tools.execute(self._search_tool, arguments)
            observation = result.output.model_dump_json() if result.output is not None else None
            state = state.with_step(
                ExecutionStep(
                    description=step.description,
                    tool_name=self._search_tool,
                    arguments=arguments,
                    observation=observation,
                    error=result.error,
                )
            )
        if not any(s.ok for s in state.steps):
            return state.fail("no evidence could be gathered")
        return state


class _SynthesizeNode(WorkflowNode[AgentState]):
    def __init__(self, llm: LLMProvider) -> None:
        super().__init__("synthesize")
        self._llm = llm

    async def run(self, state: AgentState) -> AgentState:
        evidence = "\n".join(f"- {s.description}: {s.observation}" for s in state.steps if s.ok)
        response = await self._llm.generate(
            [
                Message.system(
                    "Answer the question using only the evidence provided. "
                    "If the evidence is insufficient, say so."
                ),
                Message.user(f"Question: {state.task.goal}\n\nEvidence:\n{evidence}"),
            ]
        )
        return state.complete(response.content)


def _after_gather(state: AgentState) -> str:
    return "failed" if state.status is AgentStatus.FAILED else "synthesize"


class ResearchAgent(Agent):
    def __init__(
        self,
        *,
        engine: WorkflowEngine,
        llm: LLMProvider,
        planner: Planner,
        tools: ToolRegistry,
        search_tool: str,
    ) -> None:
        super().__init__("research", engine)
        self._llm = llm
        self._planner = planner
        self._tools = tools
        self._search_tool = search_tool

    def build_workflow(self) -> Workflow[AgentState]:
        return (
            Workflow("research", AgentState)
            .add_node(_PlanNode(self._planner))
            .add_node(_GatherNode(self._tools, self._search_tool))
            .add_node(_SynthesizeNode(self._llm))
            .set_entry("plan")
            .add_edge("plan", "gather")
            .add_conditional_edges(
                "gather", _after_gather, {"synthesize": "synthesize", "failed": END}
            )
            .add_edge("synthesize", END)
        )
