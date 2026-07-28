"""법령 조문 요약 임베딩 생성 API.

백엔드(법령 조문 임베딩 파이프라인, `LawArticleEmbeddingService`)가 조문
요약 텍스트를 벡터로 변환해달라고 요청하는 내부 엔드포인트. `app/api/shap.py`,
`app/api/fairness_internal.py` 와 동일한 컨벤션(prefix, 에러 매핑)을 따른다.

백엔드는 2xx 가 아닌 모든 응답을 `AiServerErrorException`(EA001, 502)으로,
커넥션/읽기 타임아웃(백엔드 read-timeout 120s)만 `AiServerTimeoutException`
(EA002, 504)으로 처리한다. 즉 우리는 세부 상태코드와 무관하게 항상 그 안에
non-2xx 로 응답하기만 하면 되므로, 상태코드 구분은 우리 쪽 관측용이다.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.embedding import EmbeddingGenerateRequest, EmbeddingGenerateResponse
from app.services.embedding import (
    EmbeddingConfigurationError,
    EmbeddingRequestError,
    generate_embedding,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1/embedding",
    tags=["embedding"],
)


@router.post(
    "/generate",
    response_model=EmbeddingGenerateResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "요청값 검증 실패",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "임베딩 생성 중 예상하지 못한 서버 오류",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "임베딩 제공자 호출 실패",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "임베딩 API 환경 설정 누락",
        },
    },
)
def generate_embedding_endpoint(
    request: EmbeddingGenerateRequest,
) -> EmbeddingGenerateResponse:
    """요약 텍스트 한 건을 임베딩 벡터로 변환한다."""

    try:
        return generate_embedding(request.text)
    except EmbeddingConfigurationError as exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exception),
        ) from exception
    except EmbeddingRequestError as exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exception),
        ) from exception
    except ValueError as exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exception),
        ) from exception
    except Exception as exception:
        logger.exception("예상하지 못한 임베딩 생성 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="임베딩 생성 중 서버 오류가 발생했습니다.",
        ) from exception
