"""감사 질의 응답 API."""

import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.chat.chat import ChatAnswerRequest, ChatAnswerResponse
from app.services.chat.chat import generate_chat_answer
from app.services.llm import LLMConfigurationError, LLMRequestError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1/chat",
    tags=["chat"],
)


@router.post(
    "/answers",
    response_model=ChatAnswerResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "요청값 검증 실패",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "답변 생성 중 예상하지 못한 서버 오류",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "LLM 호출 실패",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "LLM 환경 설정 누락",
        },
    },
)
def create_chat_answer(request: ChatAnswerRequest) -> ChatAnswerResponse:
    """주입된 감사 근거로 질문에 답하고 인용을 함께 돌려준다."""

    try:
        return generate_chat_answer(request)
    except LLMConfigurationError as exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exception),
        ) from exception
    except LLMRequestError as exception:
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
        logger.exception(
            "예상하지 못한 질의 응답 생성 오류: audit_id=%s",
            request.audit_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="질의 응답 생성 중 서버 오류가 발생했습니다.",
        ) from exception
