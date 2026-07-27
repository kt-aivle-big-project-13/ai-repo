"""LLM 호출 서비스 테스트.

실제 OpenAI 호출 없이 httpx.MockTransport 로 요청을 가로채 검증한다.
"""

import json

import httpx
import pytest

from app.core.config import Settings
from app.schemas.llm import ChatMessage
from app.services.llm import (
    LLMConfigurationError,
    LLMRequestError,
    complete,
    generate_chat_completion,
)


def _settings(**overrides) -> Settings:
    values = {
        "openai_api_key": "test-key",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_model": "gpt-4o-mini",
    }
    values.update(overrides)
    return Settings(**values)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_generate_chat_completion_success():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "안녕하세요"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                },
            },
        )

    result = generate_chat_completion(
        [ChatMessage(role="user", content="hi")],
        settings=_settings(),
        client=_mock_client(handler),
    )

    assert result.content == "안녕하세요"
    assert result.model == "gpt-4o-mini"
    assert result.finish_reason == "stop"
    assert result.total_tokens == 8
    # base_url 의 /v1 경로가 보존되는지 확인
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_complete_builds_system_and_user_messages():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {"message": {"content": "요약본"}, "finish_reason": "stop"}
                ],
            },
        )

    text = complete(
        "이 감사 결과를 요약해줘",
        system="너는 규제 준수 보고서 작성 도우미다",
        settings=_settings(),
        client=_mock_client(handler),
    )

    assert text == "요약본"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1]["role"] == "user"


def test_missing_api_key_raises_configuration_error():
    with pytest.raises(LLMConfigurationError):
        generate_chat_completion(
            [ChatMessage(role="user", content="hi")],
            settings=_settings(openai_api_key=None),
        )


def test_http_error_raises_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit"})

    with pytest.raises(LLMRequestError):
        generate_chat_completion(
            [ChatMessage(role="user", content="hi")],
            settings=_settings(),
            client=_mock_client(handler),
        )


def test_malformed_response_raises_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(LLMRequestError):
        generate_chat_completion(
            [ChatMessage(role="user", content="hi")],
            settings=_settings(),
            client=_mock_client(handler),
        )


def test_empty_messages_raises_request_error():
    with pytest.raises(LLMRequestError):
        generate_chat_completion([], settings=_settings())
