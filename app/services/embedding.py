"""OpenAI 호환 임베딩 생성 서비스.

법령 조문 요약 텍스트를 벡터로 변환한다. `app/services/llm.py` 와 동일하게
httpx 로 OpenAI Embeddings 규격(`POST /embeddings`)을 얇게 감싼다. API
키·베이스 URL은 LLM 설정을 공유하고, 모델만 임베딩 전용 값을 쓴다.
"""

import httpx

from app.core.config import Settings, get_settings
from app.schemas.embedding import EmbeddingGenerateResponse


class EmbeddingConfigurationError(RuntimeError):
    """임베딩 호출에 필요한 환경 설정(API 키 등)이 없는 경우."""


class EmbeddingRequestError(RuntimeError):
    """임베딩 호출·응답 처리에 실패한 경우 (타임아웃 포함).

    백엔드는 2xx가 아닌 모든 응답을 `AiServerErrorException`(EA001)으로
    처리하므로, 우리 쪽에서도 원인을 세분화하지 않고 이 예외 하나로 통일해
    항상 제한 시간 안에 non-2xx 응답을 보장하는 데 집중한다.
    """


def generate_embedding(
    text: str,
    *,
    model: str | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> EmbeddingGenerateResponse:
    """요약 텍스트 한 건을 임베딩 벡터로 변환한다.

    API 키가 없으면 `EmbeddingConfigurationError`, 호출·응답 처리 실패
    (타임아웃 포함)는 `EmbeddingRequestError`.
    """
    settings = settings or get_settings()

    if not settings.openai_api_key:
        raise EmbeddingConfigurationError("OPENAI_API_KEY 가 설정되지 않았습니다.")

    if not text.strip():
        raise ValueError("text 는 비어 있을 수 없습니다.")

    payload: dict[str, object] = {
        "model": model or settings.openai_embedding_model,
        "input": text,
    }

    url = settings.openai_base_url.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=settings.openai_timeout)

    try:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exception:
        raise EmbeddingRequestError(
            f"임베딩 호출이 상태코드 {exception.response.status_code} 로 실패했습니다: "
            f"{exception.response.text[:500]}"
        ) from exception
    except httpx.HTTPError as exception:
        raise EmbeddingRequestError(f"임베딩 호출 중 네트워크 오류: {exception}") from exception
    finally:
        if owns_client:
            client.close()

    try:
        embedding = data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exception:
        raise EmbeddingRequestError(f"임베딩 응답 형식이 올바르지 않습니다: {data}") from exception

    return EmbeddingGenerateResponse(
        embedding=embedding,
        model=data.get("model") or str(payload["model"]),
    )
