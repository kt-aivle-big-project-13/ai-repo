"""임베딩 생성 서비스 테스트.

실제 OpenAI 호출 없이 httpx.MockTransport 로 요청을 가로채 검증한다.
"""

import json

import httpx
import pytest

from app.core.config import Settings
from app.services.embedding import (
    EmbeddingConfigurationError,
    EmbeddingRequestError,
    generate_embedding,
)


def _settings(**overrides) -> Settings:
    values = {
        "openai_api_key": "test-key",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_embedding_model": "text-embedding-3-small",
    }
    values.update(overrides)
    return Settings(**values)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_generate_embedding_success():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
            },
        )

    result = generate_embedding(
        "인공지능 기본법 제1조 요약",
        settings=_settings(),
        client=_mock_client(handler),
    )

    assert result.embedding == [0.1, 0.2, 0.3]
    assert result.model == "text-embedding-3-small"
    assert captured["url"] == "https://api.openai.com/v1/embeddings"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "text-embedding-3-small"
    assert captured["body"]["input"] == "인공지능 기본법 제1조 요약"


def test_missing_api_key_raises_configuration_error():
    with pytest.raises(EmbeddingConfigurationError):
        generate_embedding("텍스트", settings=_settings(openai_api_key=None))


def test_blank_text_raises_value_error():
    with pytest.raises(ValueError):
        generate_embedding("   ", settings=_settings())


def test_http_error_raises_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    with pytest.raises(EmbeddingRequestError):
        generate_embedding(
            "텍스트",
            settings=_settings(),
            client=_mock_client(handler),
        )


def test_timeout_raises_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    with pytest.raises(EmbeddingRequestError):
        generate_embedding(
            "텍스트",
            settings=_settings(),
            client=_mock_client(handler),
        )


def test_malformed_response_raises_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with pytest.raises(EmbeddingRequestError):
        generate_embedding(
            "텍스트",
            settings=_settings(),
            client=_mock_client(handler),
        )


def test_non_json_response_raises_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json", headers={"content-type": "application/json"})

    with pytest.raises(EmbeddingRequestError):
        generate_embedding(
            "텍스트",
            settings=_settings(),
            client=_mock_client(handler),
        )


def test_invalid_embedding_type_raises_request_error():
    """embedding 필드가 숫자 리스트가 아니면(pydantic ValidationError) 422가 아닌 EmbeddingRequestError로 통일된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "data": [{"index": 0, "embedding": "not-a-vector"}],
            },
        )

    with pytest.raises(EmbeddingRequestError):
        generate_embedding(
            "텍스트",
            settings=_settings(),
            client=_mock_client(handler),
        )
