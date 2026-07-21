"""공정성 감사 실행 API (이슈 #7).

업로드된 모델·감사 데이터와 감사 설정을 받아 검증 → 위험점수·임계값 → 공정성
지표까지 통합 실행하고, 임계값과 보호속성별 공정성 지표를 반환한다.
"""

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.schemas.audit import AuditErrorResponse, AuditRunResponse, ThresholdRequest
from app.services.audit import ValidationBlockedError, run_audit

router = APIRouter(prefix="/api/fairness", tags=["fairness"])

HTTP_422_VALIDATION_BLOCKED = 422

MODEL_FILENAME = "credit_model.json"
AUDIT_FILENAME = "audit_dataset.csv"
VALID_FILENAME = "valid_processed.csv"


def _save_upload(upload: UploadFile, directory: Path, filename: str) -> Path:
    """업로드 파일을 임시 디렉터리에 저장하고 경로를 돌려준다."""
    destination = directory / filename
    with open(destination, "wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)
    return destination


@router.post(
    "/audits",
    response_model=AuditRunResponse,
    responses={HTTP_422_VALIDATION_BLOCKED: {"model": AuditErrorResponse}},
)
async def run_fairness_audit(
    model_file: UploadFile = File(description="감사 대상 XGBoost 모델 (credit_model.json)"),
    audit_dataset_file: UploadFile = File(description="감사 데이터 (audit_dataset.csv)"),
    validation_dataset_file: UploadFile | None = File(
        default=None, description="확률 보정·임계값 산출용 검증 데이터 (선택)"
    ),
    audit_name: str = Form(description="감사 이름"),
    target_approval_rate: float | None = Form(default=None),
    threshold: float | None = Form(default=None),
    sensitive_features: str | None = Form(
        default=None, description="콤마로 구분된 보호속성 컬럼명 (예: CODE_GENDER,AGE_GROUP)"
    ),
    audit_id: str | None = Form(default=None, description="백엔드가 준 감사 식별자 (선택)"),
) -> AuditRunResponse:
    """감사를 실행하고 임계값·공정성 지표를 반환한다.

    검증에서 BLOCK 이 나오면 422 로, 그 밖의 예기치 못한 오류는 500 으로 응답한다.
    임시 디렉터리는 응답 후 정리한다.
    """
    with tempfile.TemporaryDirectory(prefix="audit_") as tmp:
        tmp_dir = Path(tmp)
        model_path = _save_upload(model_file, tmp_dir, MODEL_FILENAME)
        audit_path = _save_upload(audit_dataset_file, tmp_dir, AUDIT_FILENAME)
        valid_path = (
            _save_upload(validation_dataset_file, tmp_dir, VALID_FILENAME)
            if validation_dataset_file is not None
            else None
        )

        try:
            return run_audit(
                model_path=model_path,
                audit_path=audit_path,
                audit_name=audit_name,
                valid_path=valid_path,
                threshold_request=ThresholdRequest(
                    target_approval_rate=target_approval_rate, threshold=threshold
                ),
                sensitive_features=sensitive_features,
                audit_id=audit_id,
            )
        except ValidationBlockedError as exc:
            raise HTTPException(
                status_code=HTTP_422_VALIDATION_BLOCKED,
                detail={
                    "detail": "감사 입력 검증에 실패했습니다",
                    "issues": [issue.model_dump() for issue in exc.issues],
                },
            ) from exc
