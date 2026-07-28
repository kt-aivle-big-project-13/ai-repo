"""SHAP 설명가능성 분석 API."""

import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.shap import (
    ShapAnalysisRequest,
    ShapAnalysisResponse,
)
from app.services.shap import analyze_s3_request
from app.services.storage import (
    S3ConfigurationError,
    S3DownloadError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1/shap",
    tags=["shap"],
)


@router.post(
    "/analyze",
    response_model=ShapAnalysisResponse,
    response_model_exclude_unset=True,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "요청값 또는 SHAP 입력 파일 검증 실패",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "SHAP 분석 중 예상하지 못한 서버 오류",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "S3 객체 다운로드 실패",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "S3 환경 설정 누락",
        },
    },
)

def analyze_shap(
    request: ShapAnalysisRequest,
) -> ShapAnalysisResponse:
    """S3에 저장된 모델과 감사 데이터셋으로 SHAP 분석을 실행한다."""

    try:
        return analyze_s3_request(request)
    except S3ConfigurationError as exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exception),
        ) from exception
    except S3DownloadError as exception:
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
            "예상하지 못한 SHAP 분석 오류: audit_id=%s",
            request.audit_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SHAP 분석 중 서버 오류가 발생했습니다.",
        ) from exception
