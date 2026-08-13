"""설명가능성 리포트 생성 API."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.concurrency import report_slot
from app.schemas.report.report import ReportRequest, ReportResponse
from app.services.llm import LLMConfigurationError, LLMRequestError
from app.services.report.report import ReportGenerationError, generate_explainability_report
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
    "/explainability",
    dependencies=[Depends(report_slot)],
    response_model=ReportResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "요청값 또는 분석 입력 검증 실패",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "리포트 생성 중 예상하지 못한 서버 오류",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "S3 다운로드·업로드 또는 LLM 호출 실패",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "S3 또는 LLM 환경 설정 누락, 또는 동시 실행 한도 초과",
        },
    },
)
def create_explainability_report(request: ReportRequest) -> ReportResponse:
    """S3에 저장된 모델·데이터로 설명가능성 HTML 리포트를 생성한다."""

    try:
        return generate_explainability_report(request)
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
    except (FileNotFoundError, ValueError, ReportGenerationError) as exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exception),
        ) from exception
    except Exception as exception:
        logger.exception(
            "예상하지 못한 리포트 생성 오류: audit_id=%s",
            request.audit_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="리포트 생성 중 서버 오류가 발생했습니다.",
        ) from exception
