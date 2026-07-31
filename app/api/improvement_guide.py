"""개선 권고 가이드 생성 API."""

import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.improvement_guide import (
    ImprovementGuideRequest,
    ImprovementGuideResponse,
)
from app.services.improvement_guide import generate_improvement_guide
from app.services.docx_common import DocxGenerationError
from app.services.llm import LLMConfigurationError, LLMRequestError
from app.services.report_pdf import PdfGenerationError
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
    "/improvement",
    response_model=ImprovementGuideResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "요청값 검증 실패",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "리포트 생성 중 예상하지 못한 서버 오류",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "S3 업로드 또는 LLM 호출 실패",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "S3 또는 LLM 환경 설정 누락",
        },
    },
)
def create_improvement_guide(
    request: ImprovementGuideRequest,
) -> ImprovementGuideResponse:
    """조치가 필요한 항목으로 개선 권고 가이드를 생성한다."""

    try:
        return generate_improvement_guide(request)
    except (S3ConfigurationError, LLMConfigurationError) as exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exception),
        ) from exception
    except (S3UploadError, LLMRequestError) as exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exception),
        ) from exception
    except (PdfGenerationError, DocxGenerationError) as exception:
        # 서버 쪽 렌더 실패라 요청을 고쳐도 해결되지 않으므로 500 으로 돌려준다.
        logger.exception(
            "개선 권고 가이드 문서 렌더 실패: audit_id=%s",
            request.audit_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exception),
        ) from exception
    except (FileNotFoundError, ValueError) as exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exception),
        ) from exception
    except Exception as exception:
        logger.exception(
            "예상하지 못한 개선 권고 가이드 생성 오류: audit_id=%s",
            request.audit_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="개선 권고 가이드 생성 중 서버 오류가 발생했습니다.",
        ) from exception
