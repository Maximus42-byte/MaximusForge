"""Deterministic LLM test double, so tests and examples never need a real API key."""

from __future__ import annotations

from collections.abc import Sequence

from maximusforge.core.errors import LLMProviderError
from maximusforge.llm.provider import LLMProvider
from maximusforge.llm.types import GenerationOptions, LLMResponse, Message


class ScriptedLLMProvider(LLMProvider):
    """Returns pre-scripted replies in order and records every call it receives."""

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        options: GenerationOptions | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if len(self.calls) > len(self._replies):
            raise LLMProviderError("ScriptedLLMProvider ran out of scripted replies")
        return LLMResponse(content=self._replies[len(self.calls) - 1], model="scripted")
