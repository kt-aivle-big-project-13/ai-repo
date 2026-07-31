"""규제준수 판정서 생성 API."""

import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.compliance_report import (
    ComplianceReportRequest,
    ComplianceReportResponse,
)
from app.services.compliance_report import generate_compliance_report
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
    "/compliance",
    response_model=ComplianceReportResponse,
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
def create_compliance_report(
    request: ComplianceReportRequest,
) -> ComplianceReportResponse:
    """자가점검 응답과 법령 매핑으로 규제준수 판정서를 생성한다."""

    try:
        return generate_compliance_report(request)
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
            "규제준수 판정서 문서 렌더 실패: audit_id=%s",
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
            "예상하지 못한 규제준수 판정서 생성 오류: audit_id=%s",
            request.audit_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="규제준수 판정서 생성 중 서버 오류가 발생했습니다.",
        ) from exception
