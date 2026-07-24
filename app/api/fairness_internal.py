"""공정성 감사 내부 연동 API (S3 기반).

백엔드가 S3 Key로 공정성 감사를 요청하는 엔드포인트. 계산은 기존 `run_audit`
을 재사용하며, 입력만 S3 다운로드로 받는다 (`app/api/shap.py` 와 동일 패턴).
파일 업로드 방식(`app/api/fairness.py`, `POST /api/fairness/audits`)은 그대로 유지한다.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.audit import AuditRunResponse
from app.schemas.fairness_internal import FairnessAnalyzeRequest
from app.services.audit import ValidationBlockedError
from app.services.fairness_analysis import analyze_s3_request
from app.services.storage import S3ConfigurationError, S3DownloadError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1/fairness",
    tags=["fairness-internal"],
)


@router.post(
    "/analyze",
    response_model=AuditRunResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "요청값 또는 감사 입력 파일 검증 실패",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "공정성 분석 중 예상하지 못한 서버 오류",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "S3 객체 다운로드 실패",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "S3 환경 설정 누락",
        },
    },
)
def analyze_fairness(request: FairnessAnalyzeRequest) -> AuditRunResponse:
    """S3에 저장된 모델·데이터셋으로 공정성 감사를 실행한다."""

    try:
        return analyze_s3_request(request)
    except ValidationBlockedError as exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "detail": "감사 입력 검증에 실패했습니다",
                "issues": [issue.model_dump() for issue in exception.issues],
            },
        ) from exception
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
            "예상하지 못한 공정성 분석 오류: audit_id=%s",
            request.audit_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="공정성 분석 중 서버 오류가 발생했습니다.",
        ) from exception
