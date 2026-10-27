"""Registry that owns tools and executes them with validation and uniform error reporting."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from maximusforge.core.errors import DuplicateToolError
from maximusforge.tools.base import Tool, ToolSpec


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outcome of one tool invocation. Failures are data, not exceptions.

    Tool names and arguments typically originate from model output, so an unknown tool,
    invalid arguments, or an exception inside the tool are all reported as ``ok=False``
    rather than raised.
    """

    tool_name: str
    ok: bool
    output: BaseModel | None = None
    error: str | None = None
    duration_ms: float = 0.0


class ToolRegistry:
    """Instance-scoped (no global state) collection of tools, keyed by unique name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}

    def register(self, tool: Tool[Any, Any], *, replace: bool = False) -> None:
        """Register ``tool``. A name collision raises unless ``replace=True``."""
        if tool.name in self._tools and not replace:
            raise DuplicateToolError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any, Any] | None:
        return self._tools.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def specs(self) -> list[ToolSpec]:
        """Tool specs sorted by name, so output is deterministic."""
        return [self._tools[name].spec() for name in sorted(self._tools)]

    async def execute(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(tool_name=name, ok=False, error=f"unknown tool {name!r}")
        try:
            parsed = tool.input_model.model_validate(dict(arguments))
        except ValidationError:
            return ToolResult(tool_name=name, ok=False, error="invalid arguments")

        started = time.perf_counter()
        try:
            output = await tool.run(parsed)
            if not isinstance(output, BaseModel):
                raise TypeError("tool returned unstructured output")
        except Exception as exc:  # tool code is untrusted; any failure becomes a result
            elapsed = (time.perf_counter() - started) * 1000
            error = f"tool raised {type(exc).__name__}"
            return ToolResult(tool_name=name, ok=False, error=error, duration_ms=elapsed)
        elapsed = (time.perf_counter() - started) * 1000
        return ToolResult(tool_name=name, ok=True, output=output, duration_ms=elapsed)
