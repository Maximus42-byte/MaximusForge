from __future__ import annotations

from typing import cast

import pytest

from maximusforge.core import DuplicateToolError
from maximusforge.tools import ToolRegistry

from .conftest import FakeSearchTool, SearchInput, SearchOutput


def test_register_and_lookup() -> None:
    registry = ToolRegistry()
    tool = FakeSearchTool()
    registry.register(tool)
    assert "search" in registry
    assert registry.get("search") is tool
    assert len(registry) == 1


def test_duplicate_registration_raises_and_keeps_original() -> None:
    registry = ToolRegistry()
    first = FakeSearchTool()
    registry.register(first)
    with pytest.raises(DuplicateToolError):
        registry.register(FakeSearchTool())
    assert registry.get("search") is first


def test_replace_true_overwrites() -> None:
    registry = ToolRegistry()
    registry.register(FakeSearchTool())
    second = FakeSearchTool()
    registry.register(second, replace=True)
    assert registry.get("search") is second


def test_specs_expose_schema_sorted_by_name() -> None:
    registry = ToolRegistry()
    registry.register(FakeSearchTool())
    (spec,) = registry.specs()
    assert spec.name == "search"
    assert spec.input_schema["properties"]["query"]["type"] == "string"


async def test_execute_success_returns_typed_output_and_metadata() -> None:
    registry = ToolRegistry()
    registry.register(FakeSearchTool())
    result = await registry.execute("search", {"query": "cats"})
    assert result.ok
    assert result.output == SearchOutput(snippets=["snippet about cats"])
    assert result.duration_ms >= 0


async def test_unknown_tool_is_a_failed_result_not_an_exception() -> None:
    result = await ToolRegistry().execute("nope", {})
    assert not result.ok
    assert "unknown tool" in (result.error or "")


async def test_invalid_arguments_are_a_failed_result() -> None:
    registry = ToolRegistry()
    registry.register(FakeSearchTool())
    result = await registry.execute("search", {"wrong": 1})
    assert not result.ok
    assert "invalid arguments" in (result.error or "")


async def test_tool_exception_is_a_failed_result() -> None:
    registry = ToolRegistry()
    registry.register(FakeSearchTool())
    result = await registry.execute("search", {"query": "boom"})
    assert not result.ok
    assert "RuntimeError" in (result.error or "")


async def test_invalid_arguments_do_not_echo_values() -> None:
    registry = ToolRegistry()
    registry.register(FakeSearchTool())
    result = await registry.execute("search", {"query": {"secret": "sk-secret"}})
    assert not result.ok
    assert "sk-secret" not in (result.error or "")


async def test_unstructured_tool_output_is_a_failed_result() -> None:
    class BrokenOutputTool(FakeSearchTool):
        async def run(self, input: SearchInput) -> SearchOutput:
            return cast(SearchOutput, "unstructured")

    registry = ToolRegistry()
    registry.register(BrokenOutputTool())
    result = await registry.execute("search", {"query": "cats"})
    assert not result.ok
    assert result.output is None
    assert "TypeError" in (result.error or "")
