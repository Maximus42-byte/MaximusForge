"""Tool abstraction: a named capability with validated, structured input and output."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Provider-neutral description of a tool, suitable for presenting to an LLM."""

    name: str
    description: str
    input_schema: dict[str, Any]


class Tool(ABC, Generic[InputT, OutputT]):
    """A capability an agent can invoke.

    Subclasses set ``name``, ``description`` and ``input_model`` as class attributes and
    implement :meth:`run`. Inputs reach ``run`` already validated against ``input_model``.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    @abstractmethod
    async def run(self, input: InputT) -> OutputT:
        """Execute the tool. Raise any exception to signal failure; the registry records it."""

    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.name,
            description=cls.description,
            input_schema=cls.input_model.model_json_schema(),
        )
