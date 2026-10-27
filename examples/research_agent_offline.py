"""Run the minimal ResearchAgent end to end with no network, API key, or database.

    python examples/research_agent_offline.py

A scripted LLM and a canned search tool stand in for real ones. To use a real model, replace
``ScriptedLLMProvider`` with ``OpenAICompatibleProvider(OpenAICompatibleSettings.from_env())``.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from maximusforge.agents import ResearchAgent
from maximusforge.core import Task
from maximusforge.llm.testing import ScriptedLLMProvider
from maximusforge.planning import LLMPlanner
from maximusforge.tools import Tool, ToolRegistry
from maximusforge.workflows.engines.langgraph_engine import LangGraphEngine


class SearchInput(BaseModel):
    query: str


class SearchOutput(BaseModel):
    snippets: list[str]


class CannedSearch(Tool[SearchInput, SearchOutput]):
    name = "search"
    description = "Returns a canned snippet for any query."
    input_model = SearchInput

    async def run(self, input: SearchInput) -> SearchOutput:
        return SearchOutput(snippets=[f"(canned result for {input.query!r})"])


async def main() -> None:
    llm = ScriptedLLMProvider(
        [
            "history of graph workflows\nhow agents use graphs",  # planner reply
            "Graph workflows model agent logic as nodes and edges.",  # synthesis reply
        ]
    )
    tools = ToolRegistry()
    tools.register(CannedSearch())

    agent = ResearchAgent(
        engine=LangGraphEngine(),
        llm=llm,
        planner=LLMPlanner(llm),
        tools=tools,
        search_tool="search",
    )
    result = await agent.run(Task(goal="Why do agent frameworks use graphs?"))

    print("status:", result.state.status.value)
    print("trace: ", " -> ".join(result.trace))
    print("answer:", result.state.output)


if __name__ == "__main__":
    asyncio.run(main())
