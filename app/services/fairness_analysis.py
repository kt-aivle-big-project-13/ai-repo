"""공정성 감사 내부 연동 서비스 (S3 기반).

백엔드가 넘긴 S3 Key로 모델·감사 데이터(·검증 데이터)를 내려받아 기존 감사
로직(`run_audit`)을 그대로 실행한다. 즉 계산 로직은 재사용하고, 입력을 파일
업로드가 아닌 S3 다운로드로 바꾸는 어댑터다 (`app/services/shap.py` 와 동일 패턴).
"""

import tempfile
from pathlib import Path

from app.schemas.audit import AuditInputSource, AuditRunResponse, ThresholdRequest
from app.schemas.fairness_internal import FairnessAnalyzeRequest
from app.services.audit import run_audit
from app.services.storage import download_s3_object


def _build_threshold_request(request: FairnessAnalyzeRequest) -> ThresholdRequest:
    """S3 요청의 임계값 필드를 감사용 `ThresholdRequest` 로 옮긴다.

    수동값이 있으면 우선하고, 없으면 목표 승인율을 쓴다 (둘 다 없으면
    ThresholdRequest 기본값 → run_audit 이 목표 승인율 0.90 으로 처리).
    """
    return ThresholdRequest(
        target_approval_rate=request.target_approval_rate,
        threshold=request.manual_threshold,
    )


def analyze_s3_request(
    request: FairnessAnalyzeRequest,
    include_report_meta: bool = False,
) -> AuditRunResponse:
    """S3 파일을 내려받아 공정성 감사를 실행하고 임시 파일을 정리한다.

    `include_report_meta=True` 면 편향 리포트용 메타·증적도 함께 채운다(기본 off라
    백엔드 연동 경로는 영향 없음).
    """

    with tempfile.TemporaryDirectory(
        prefix=f"fairness_audit_{request.audit_id}_"
    ) as temporary_directory:
        temporary_path = Path(temporary_directory)

        model_suffix = Path(request.model_s3_key).suffix or ".json"
        audit_suffix = Path(request.audit_dataset_s3_key).suffix or ".csv"

        model_path = temporary_path / f"model{model_suffix}"
        audit_path = temporary_path / f"audit_dataset{audit_suffix}"

        download_s3_object(request.model_s3_key, model_path)
        download_s3_object(request.audit_dataset_s3_key, audit_path)

        valid_path: Path | None = None
        if request.validation_dataset_s3_key:
            valid_suffix = Path(request.validation_dataset_s3_key).suffix or ".csv"
            valid_path = temporary_path / f"valid_processed{valid_suffix}"
            download_s3_object(request.validation_dataset_s3_key, valid_path)

        return run_audit(
            model_path=model_path,
            audit_path=audit_path,
            audit_name=request.audit_name,
            valid_path=valid_path,
            threshold_request=_build_threshold_request(request),
            sensitive_features=",".join(request.sensitive_features),
            audit_id=str(request.audit_id),
            include_report_meta=include_report_meta,
            report_source=(
                AuditInputSource(
                    model_s3_key=request.model_s3_key,
                    audit_dataset_s3_key=request.audit_dataset_s3_key,
                    validation_dataset_s3_key=request.validation_dataset_s3_key,
                )
                if include_report_meta
                else None
            ),
        )
