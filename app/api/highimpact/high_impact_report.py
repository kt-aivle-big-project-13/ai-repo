"""고영향 AI 사전진단 보고서 생성 API."""

import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.highimpact.high_impact_report import (
    HighImpactReportRequest,
    HighImpactReportResponse,
)
from app.services.highimpact.high_impact_report import (
    HighImpactReportGenerationError,
    generate_high_impact_report,
)
from app.services.storage import (
    S3ConfigurationError,
    S3UploadError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1/reports",
    tags=["reports"],
)


@router.post(
    "/high-impact-assessment",
    response_model=HighImpactReportResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "사전진단 요청값 검증 실패",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "PDF·Word 보고서 생성 실패",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "S3 업로드 실패",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "S3 환경 설정 누락",
        },
    },
)
def create_high_impact_report(
    request: HighImpactReportRequest,
) -> HighImpactReportResponse:
    """사전진단 결과를 PDF·Word로 생성해 S3에 저장한다."""

    try:
        return generate_high_impact_report(request)
    except S3ConfigurationError as exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exception),
        ) from exception
    except S3UploadError as exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exception),
        ) from exception
    except HighImpactReportGenerationError as exception:
        logger.exception(
            "고영향 AI 사전진단 보고서 생성 실패: "
            "audit_id=%s, assessment_id=%s",
            request.audit_id,
            request.assessment_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exception),
        ) from exception
    except Exception as exception:
        logger.exception(
            "예상하지 못한 고영향 AI 사전진단 보고서 오류: "
            "audit_id=%s, assessment_id=%s",
            request.audit_id,
            request.assessment_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "고영향 AI 사전진단 보고서 생성 중 "
                "서버 오류가 발생했습니다."
            ),
        ) from exception