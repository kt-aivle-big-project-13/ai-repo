"""편향진단 리포트 생성 API."""

import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.bias_report import BiasReportRequest, BiasReportResponse
from app.services.audit import ValidationBlockedError
from app.services.bias_report import generate_bias_report
from app.services.llm import LLMConfigurationError, LLMRequestError
from app.services.storage import (
    S3ConfigurationError,
    S3DownloadError,
    S3UploadError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1/reports",
    tags=["reports"],
)


@router.post(
    "/bias",
    response_model=BiasReportResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "요청값 또는 감사 입력 검증 실패",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "리포트 생성 중 예상하지 못한 서버 오류",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "S3 다운로드·업로드 또는 LLM 호출 실패",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "S3 또는 LLM 환경 설정 누락",
        },
    },
)
def create_bias_report(request: BiasReportRequest) -> BiasReportResponse:
    """S3에 저장된 모델·데이터로 편향진단 HTML 리포트를 생성한다."""

    try:
        return generate_bias_report(request)
    except ValidationBlockedError as exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "detail": "감사 입력 검증에 실패했습니다",
                "issues": [issue.model_dump() for issue in exception.issues],
            },
        ) from exception
    except (S3ConfigurationError, LLMConfigurationError) as exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exception),
        ) from exception
    except (S3DownloadError, S3UploadError, LLMRequestError) as exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exception),
        ) from exception
    except (FileNotFoundError, ValueError) as exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exception),
        ) from exception
    except Exception as exception:
        logger.exception(
            "예상하지 못한 편향 리포트 생성 오류: audit_id=%s",
            request.audit_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="편향 리포트 생성 중 서버 오류가 발생했습니다.",
        ) from exception
