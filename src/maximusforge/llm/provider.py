"""The interface agents depend on instead of any concrete LLM vendor (ADR-004)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from maximusforge.llm.types import GenerationOptions, LLMResponse, Message


class LLMProvider(ABC):
    """A source of chat completions.

    Implementations translate to and from a vendor or local-server wire format and must raise
    :class:`~maximusforge.core.errors.LLMProviderError` on failure so callers never see
    vendor-specific exceptions.

    Streaming and native tool-calling are intentionally not part of this interface yet; they
    will be added as separate, optional capabilities when a node needs them.
    """

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[Message],
        *,
        options: GenerationOptions | None = None,
    ) -> LLMResponse:
        """Return a single completion for ``messages``."""
