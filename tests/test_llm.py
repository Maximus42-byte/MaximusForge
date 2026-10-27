from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from maximusforge.core import ConfigurationError, LLMProviderError
from maximusforge.llm import (
    GenerationOptions,
    LLMProvider,
    Message,
    OpenAICompatibleProvider,
    OpenAICompatibleSettings,
)
from maximusforge.llm.testing import ScriptedLLMProvider


async def test_scripted_provider_satisfies_interface_and_records_calls() -> None:
    llm: LLMProvider = ScriptedLLMProvider(["one", "two"])
    assert (await llm.generate([Message.user("a")])).content == "one"
    assert (await llm.generate([Message.user("b")])).content == "two"
    assert isinstance(llm, ScriptedLLMProvider)
    assert [c[0].content for c in llm.calls] == ["a", "b"]


async def test_scripted_provider_raises_when_exhausted() -> None:
    llm = ScriptedLLMProvider([])
    with pytest.raises(LLMProviderError):
        await llm.generate([Message.user("a")])


def _provider(
    handler: httpx.MockTransport, api_key: str | None = None
) -> OpenAICompatibleProvider:
    settings = OpenAICompatibleSettings(
        model="m",
        base_url="http://llm.test/v1",
        api_key=SecretStr(api_key) if api_key else None,
    )
    return OpenAICompatibleProvider(settings, client=httpx.AsyncClient(transport=handler))


async def test_openai_compatible_request_and_response_mapping() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "m-1",
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    provider = _provider(httpx.MockTransport(handler), api_key="sk-test")
    response = await provider.generate(
        [Message.system("s"), Message.user("u")],
        options=GenerationOptions(temperature=0.2, max_tokens=16),
    )

    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"] == {
        "model": "m",
        "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        "temperature": 0.2,
        "max_tokens": 16,
    }
    assert response.content == "hi"
    assert response.finish_reason == "stop"
    assert response.usage is not None
    assert response.usage.total_tokens == 5


async def test_no_authorization_header_without_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    response = await _provider(httpx.MockTransport(handler)).generate([Message.user("x")])
    assert response.content == "ok"


async def test_http_error_becomes_provider_error_without_leaking_key() -> None:
    provider = _provider(
        httpx.MockTransport(lambda r: httpx.Response(401, text="bad key sk-secret")),
        api_key="sk-secret",
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate([Message.user("x")])
    assert "401" in str(info.value)
    assert "sk-secret" not in str(info.value)


async def test_malformed_response_becomes_provider_error() -> None:
    provider = _provider(httpx.MockTransport(lambda r: httpx.Response(200, json={"nope": 1})))
    with pytest.raises(LLMProviderError):
        await provider.generate([Message.user("x")])


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"choices": [{"message": {"content": 123}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": "x"}},
    ],
)
async def test_invalid_response_shape_becomes_provider_error(body: object) -> None:
    provider = _provider(httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(LLMProviderError):
        await provider.generate([Message.user("x")])


async def test_transport_failure_becomes_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMProviderError):
        await _provider(httpx.MockTransport(handler)).generate([Message.user("x")])


def test_settings_from_env() -> None:
    settings = OpenAICompatibleSettings.from_env(
        {
            "MAXIMUSFORGE_LLM_MODEL": "llama3",
            "MAXIMUSFORGE_LLM_BASE_URL": "http://localhost:11434/v1",
            "MAXIMUSFORGE_LLM_API_KEY": "sk-abc",
        }
    )
    assert settings.model == "llama3"
    assert settings.base_url == "http://localhost:11434/v1"
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "sk-abc"


def test_settings_require_model_but_not_key() -> None:
    with pytest.raises(ConfigurationError):
        OpenAICompatibleSettings.from_env({})
    assert OpenAICompatibleSettings.from_env({"MAXIMUSFORGE_LLM_MODEL": "m"}).api_key is None


def test_api_key_is_not_in_repr() -> None:
    settings = OpenAICompatibleSettings(model="m", api_key=SecretStr("sk-secret"))
    assert "sk-secret" not in repr(settings)
    assert "sk-secret" not in str(settings)


def test_base_url_with_credentials_is_rejected_without_echoing_secret() -> None:
    with pytest.raises(ConfigurationError) as info:
        OpenAICompatibleSettings(model="m", base_url="https://user:sk-secret@llm.test/v1")
    assert "sk-secret" not in str(info.value)


async def test_request_error_does_not_echo_base_url() -> None:
    provider = OpenAICompatibleProvider(
        OpenAICompatibleSettings(model="m", base_url="https://llm.test/secret-path"),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ),
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate([Message.user("x")])
    assert "secret-path" not in str(info.value)
