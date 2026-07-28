"""임베딩 생성 FastAPI 엔드포인트 테스트."""

from fastapi.testclient import TestClient

import app.api.embedding as embedding_api
from app.main import app
from app.schemas.embedding import EmbeddingGenerateResponse
from app.services.embedding import EmbeddingConfigurationError, EmbeddingRequestError

client = TestClient(app)


def test_generate_embedding_returns_backend_contract(monkeypatch):
    """응답이 백엔드 EmbeddingResponse(embedding, model)와 동일한 형태다."""

    def fake_generate(text):
        assert text == "인공지능 기본법 제1조 요약"
        return EmbeddingGenerateResponse(
            embedding=[0.1, 0.2, 0.3],
            model="text-embedding-3-small",
        )

    monkeypatch.setattr(embedding_api, "generate_embedding", fake_generate)

    response = client.post(
        "/internal/v1/embedding/generate",
        json={"text": "인공지능 기본법 제1조 요약"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "embedding": [0.1, 0.2, 0.3],
        "model": "text-embedding-3-small",
    }


def test_generate_embedding_rejects_blank_text():
    response = client.post(
        "/internal/v1/embedding/generate",
        json={"text": ""},
    )

    assert response.status_code == 422


def test_generate_embedding_returns_503_when_not_configured(monkeypatch):
    def fail_generate(text):
        raise EmbeddingConfigurationError("OPENAI_API_KEY 가 설정되지 않았습니다.")

    monkeypatch.setattr(embedding_api, "generate_embedding", fail_generate)

    response = client.post(
        "/internal/v1/embedding/generate",
        json={"text": "텍스트"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "OPENAI_API_KEY 가 설정되지 않았습니다."}


def test_generate_embedding_returns_502_when_provider_call_fails(monkeypatch):
    def fail_generate(text):
        raise EmbeddingRequestError("임베딩 호출이 상태코드 500 로 실패했습니다.")

    monkeypatch.setattr(embedding_api, "generate_embedding", fail_generate)

    response = client.post(
        "/internal/v1/embedding/generate",
        json={"text": "텍스트"},
    )

    assert response.status_code == 502
