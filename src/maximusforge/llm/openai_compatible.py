"""Reference adapter for servers speaking the OpenAI ``/chat/completions`` wire format.

One adapter covers OpenAI itself and most local/self-hosted servers (Ollama, vLLM, LM Studio,
llama.cpp server). Provider-native adapters (Anthropic, Gemini, ...) are future work.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from maximusforge.core.errors import ConfigurationError, LLMProviderError
from maximusforge.llm.provider import LLMProvider
from maximusforge.llm.types import GenerationOptions, LLMResponse, Message, TokenUsage

ENV_BASE_URL = "MAXIMUSFORGE_LLM_BASE_URL"
ENV_MODEL = "MAXIMUSFORGE_LLM_MODEL"
ENV_API_KEY = "MAXIMUSFORGE_LLM_API_KEY"


class OpenAICompatibleSettings(BaseModel):
    """Connection settings. Nothing is read from the environment unless :meth:`from_env` is used."""

    model_config = ConfigDict(frozen=True)

    model: str
    base_url: str = Field(default="https://api.openai.com/v1", repr=False)
    api_key: SecretStr | None = None  # local servers typically need none
    timeout_seconds: float = 60.0

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            parts = urlsplit(value)
        except ValueError as exc:
            raise ConfigurationError("invalid LLM base URL") from exc
        if (parts.scheme not in {"http", "https"} or not parts.netloc
                or parts.username or parts.password or parts.query or parts.fragment):
            raise ConfigurationError("LLM base URL must be HTTP(S) without credentials or query")
        return value

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = os.environ if environ is None else environ
        model = env.get(ENV_MODEL)
        if not model:
            raise ConfigurationError(f"{ENV_MODEL} is not set")
        values: dict[str, Any] = {"model": model}
        if base_url := env.get(ENV_BASE_URL):
            values["base_url"] = base_url
        if api_key := env.get(ENV_API_KEY):
            values["api_key"] = SecretStr(api_key)
        return cls(**values)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        settings: OpenAICompatibleSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client  # injectable for tests; otherwise one is opened per request

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        options: GenerationOptions | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
        }
        if options is not None:
            if options.temperature is not None:
                payload["temperature"] = options.temperature
            if options.max_tokens is not None:
                payload["max_tokens"] = options.max_tokens

        headers: dict[str, str] = {}
        if self._settings.api_key is not None:
            headers["Authorization"] = f"Bearer {self._settings.api_key.get_secret_value()}"
        url = f"{self._settings.base_url.rstrip('/')}/chat/completions"

        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._settings.timeout_seconds) as client:
                    response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return self._parse(response.json())
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"LLM server returned HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMProviderError(f"LLM request failed: {type(exc).__name__}") from exc

    @staticmethod
    def _parse(body: object) -> LLMResponse:
        if not isinstance(body, Mapping):
            raise LLMProviderError("LLM response must be an object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise LLMProviderError("LLM response did not contain a completion")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise LLMProviderError("LLM response did not contain text content")
        model = body.get("model")
        finish_reason = choice.get("finish_reason")
        if (model is not None and not isinstance(model, str)) or (
            finish_reason is not None and not isinstance(finish_reason, str)
        ):
            raise LLMProviderError("LLM response metadata is invalid")
        usage = None
        raw_usage = body.get("usage")
        if raw_usage is not None:
            if not isinstance(raw_usage, Mapping):
                raise LLMProviderError("LLM response usage is invalid")
            prompt_tokens = raw_usage.get("prompt_tokens")
            completion_tokens = raw_usage.get("completion_tokens")
            if (
                not isinstance(prompt_tokens, int)
                or isinstance(prompt_tokens, bool)
                or prompt_tokens < 0
                or not isinstance(completion_tokens, int)
                or isinstance(completion_tokens, bool)
                or completion_tokens < 0
            ):
                raise LLMProviderError("LLM response usage is invalid")
            usage = TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return LLMResponse(
            content=message["content"],
            model=model,
            finish_reason=finish_reason,
            usage=usage,
        )
