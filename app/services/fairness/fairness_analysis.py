"""공정성 감사 내부 연동 서비스 (S3 기반).

백엔드가 넘긴 S3 Key로 모델·감사 데이터(·검증 데이터)를 내려받아 기존 감사
로직(`run_audit`)을 그대로 실행한다. 즉 계산 로직은 재사용하고, 입력을 파일
업로드가 아닌 S3 다운로드로 바꾸는 어댑터다 (`app/services/shap.py` 와 동일 패턴).
"""

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.audit import AuditInputSource, AuditRunResponse, ThresholdRequest
from app.schemas.fairness.fairness_internal import FairnessAnalyzeRequest
from app.services.audit import run_audit
from app.services.storage import (
    S3ConfigurationError,
    S3UploadError,
    download_s3_object,
    upload_s3_object,
)

logger = logging.getLogger(__name__)

# 감사 결과를 남기는 위치. 편향 리포트가 같은 규칙으로 찾아 읽는다
# (`app/services/bias/bias_report.py`).
ARTIFACT_PREFIX_ROOT = "fairness"
RESULT_FILE_NAME = "audit_result.json"


def _build_threshold_request(request: FairnessAnalyzeRequest) -> ThresholdRequest:
    """S3 요청의 임계값 필드를 감사용 `ThresholdRequest` 로 옮긴다.

    수동값이 있으면 우선하고, 없으면 목표 승인율을 쓴다 (둘 다 없으면
    ThresholdRequest 기본값 → run_audit 이 목표 승인율 0.90 으로 처리).
    """
    return ThresholdRequest(
        target_approval_rate=request.target_approval_rate,
        threshold=request.manual_threshold,
    )


def _persist_result(audit_id: int, result: AuditRunResponse) -> None:
    """감사 결과를 S3 에 남겨 편향 리포트가 재사용할 수 있게 한다.

    같은 초에 재시도가 겹쳐도 서로 덮어쓰지 않도록 run_id 에 초 단위 시각과 함께
    감사 실행 식별자를 붙인다.

    업로드 실패는 삼킨다. 결과는 이미 응답으로 돌아가므로 분석 자체는 성공한
    것이고, 편향 리포트는 산출물이 없으면 직접 계산하는 경로로 넘어간다.
    """

    run_id = (
        f"fairness_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"_{result.audit_id}"
    )
    key = f"{ARTIFACT_PREFIX_ROOT}/{audit_id}/{run_id}/{RESULT_FILE_NAME}"

    try:
        with tempfile.TemporaryDirectory(prefix=f"fairness_result_{audit_id}_") as tmp:
            path = Path(tmp) / RESULT_FILE_NAME
            path.write_text(
                result.model_dump_json(indent=2),
                encoding="utf-8",
            )
            upload_s3_object(path, key)
    except (S3UploadError, S3ConfigurationError, OSError) as exception:
        logger.warning(
            "공정성 감사 결과 저장 실패: audit_id=%s, key=%s (%s)",
            audit_id,
            key,
            exception,
        )


def analyze_s3_request(
    request: FairnessAnalyzeRequest,
    include_report_meta: bool = False,
    persist_result: bool = False,
) -> AuditRunResponse:
    """S3 파일을 내려받아 공정성 감사를 실행하고 임시 파일을 정리한다.

    `include_report_meta=True` 면 편향 리포트용 메타·증적도 함께 채운다(기본 off라
    백엔드 연동 경로는 영향 없음).

    `persist_result=True` 면 결과를 S3 에 남겨 편향 리포트가 같은 계산을 다시 하지
    않도록 한다.
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

        result = run_audit(
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

        if persist_result:
            _persist_result(request.audit_id, result)

        return result
