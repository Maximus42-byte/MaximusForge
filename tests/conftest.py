from __future__ import annotations

import pytest
from pydantic import BaseModel

from maximusforge.tools import Tool
from maximusforge.workflows.engine import WorkflowEngine
from maximusforge.workflows.engines.langgraph_engine import LangGraphEngine

_CREDENTIAL_ENV_VARS = (
    "MAXIMUSFORGE_LLM_BASE_URL",
    "MAXIMUSFORGE_LLM_MODEL",
    "MAXIMUSFORGE_LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test can depend on, or leak, real credentials."""
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def engine() -> WorkflowEngine:
    """The engine under test. A future native engine should be added here as a second param
    so every contract test runs against both implementations."""
    return LangGraphEngine()


class SearchInput(BaseModel):
    query: str


class SearchOutput(BaseModel):
    snippets: list[str]


class FakeSearchTool(Tool[SearchInput, SearchOutput]):
    name = "search"
    description = "Fake search returning one canned snippet per query."
    input_model = SearchInput

    async def run(self, input: SearchInput) -> SearchOutput:
        if "boom" in input.query:
            raise RuntimeError("search backend exploded")
        return SearchOutput(snippets=[f"snippet about {input.query}"])
