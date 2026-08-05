"""감사 실행 오케스트레이션 (이슈 #7).

검증(#4) → 위험점수·임계값(#5) → 공정성 지표(#6)를 순서대로 호출해 하나의 감사
결과로 조립한다. 각 단계는 앞 단계의 산출물을 그대로 받아 쓰므로 같은 계산을
반복하지 않는다.

검증에서 BLOCK 이 나오면 이후 단계를 실행하지 않고 `ValidationBlockedError` 를
올린다 — 서버 오류와 구분해 API 가 다른 상태 코드로 응답하기 위함이다.
"""

import hashlib
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import xgboost as xgb

from app.schemas.audit import (
    AuditInputSource,
    AuditReportMeta,
    AuditRunResponse,
    FairnessMetricValues,
    ThresholdRequest,
)
from app.schemas.fairness.fairness import AttributeFairness
from app.schemas.scoring import ThresholdConfig, ThresholdMethod
from app.schemas.validation import IssueLevel, ValidationIssue, ValidationResult
from app.services.fairness.fairness import compute_fairness_metrics
from app.services.performance import compute_performance
from app.services.scoring import actual_defaults, load_audit_frames, score_audit_dataset
from app.services.validation import validate_audit_inputs

# 편향 리포트용 고정 한계 문구. 공정성 지표 해석 시 반드시 함께 제시한다.
FAIRNESS_LIMITATIONS = [
    "공정성 지표는 집단 간 결과 격차를 나타내며 인과관계를 의미하지 않음",
    "표본이 최소 기준 미만인 집단은 지표 계산에서 제외됨",
    "대리변수(proxy)를 통한 간접 차별은 이 지표들로 직접 측정되지 않음",
    "판정 임계값은 정책 영역으로 이 리포트에는 raw 값·격차만 제시함",
]


class ValidationBlockedError(Exception):
    """검증 BLOCK 으로 감사를 진행할 수 없을 때 올린다."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("감사 입력 검증에서 BLOCK 이 발생했습니다")


def build_threshold_config(request: ThresholdRequest | None) -> ThresholdConfig:
    """요청의 임계값 설정을 채점용 `ThresholdConfig` 로 바꾼다.

    수동 임계값이 있으면 우선하고, 없으면 목표 승인율로 산출한다. 둘 다 없으면
    `ThresholdConfig` 기본값(목표 승인율 0.90)을 쓴다.
    """
    if request is None:
        return ThresholdConfig()
    if request.threshold is not None:
        return ThresholdConfig(
            method=ThresholdMethod.MANUAL, manual_threshold=request.threshold
        )
    if request.target_approval_rate is not None:
        return ThresholdConfig(
            method=ThresholdMethod.TARGET_APPROVAL_RATE,
            target_approval_rate=request.target_approval_rate,
        )
    return ThresholdConfig()


def _parse_sensitive_features(raw: str | None) -> list[str] | None:
    """콤마로 구분된 민감변수 문자열을 컬럼명 리스트로 바꾼다."""
    if not raw:
        return None
    columns = [item.strip() for item in raw.split(",") if item.strip()]
    return columns or None


def _summarize_fairness(
    fairness_by_attribute: dict[str, AttributeFairness],
) -> dict[str, FairnessMetricValues]:
    """보호속성별 지표를 백엔드 FairnessMetricCode 키로 정리한다."""
    return {
        attribute: FairnessMetricValues(
            DEMOGRAPHIC_PARITY=result.demographic_parity_difference,
            EQUAL_OPPORTUNITY=result.equal_opportunity_difference,
            EQUALIZED_ODDS=result.equalized_odds_difference,
            PROPORTIONAL_PARITY=result.proportional_parity_ratio,
            FPR_PARITY=result.fpr_parity_difference,
            FDR_PARITY=result.fdr_parity_difference,
            FOR_PARITY=result.for_parity_difference,
        )
        for attribute, result in fairness_by_attribute.items()
    }


def _sha256(path: Path) -> str:
    """파일 콘텐츠의 SHA-256. 실제 사용한 입력 바이트를 특정하는 증적."""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_version() -> str | None:
    """실행 코드 버전(git commit). 배포 환경변수 우선, 없으면 git, 그것도 없으면 None."""
    for name in ("APP_GIT_SHA", "GIT_COMMIT", "GIT_SHA"):
        value = os.getenv(name)
        if value:
            return value
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _build_report_meta(
    validation: ValidationResult,
    model_path: Path,
    audit_path: Path,
    valid_path: Path | None,
    report_source: AuditInputSource | None,
) -> AuditReportMeta:
    """검증 결과가 이미 계산해 둔 모델·데이터·스키마 정보와 원본 입력 식별자·해시를
    리포트 메타로 옮긴다."""
    schema = validation.model_schema_info
    dataset = validation.audit_dataset
    now = datetime.now(timezone.utc)
    source = report_source or AuditInputSource()
    # 식별용 표시명은 원본 S3 Key 의 basename 을 우선 쓴다(임시 다운로드 파일명이 아니라).
    display_name = (
        Path(source.model_s3_key).name if source.model_s3_key else Path(model_path).name
    )
    return AuditReportMeta(
        model_s3_key=source.model_s3_key,
        audit_dataset_s3_key=source.audit_dataset_s3_key,
        validation_dataset_s3_key=source.validation_dataset_s3_key,
        model_sha256=_sha256(model_path),
        audit_dataset_sha256=_sha256(audit_path),
        validation_dataset_sha256=_sha256(valid_path) if valid_path else None,
        code_version=_code_version(),
        model_file=display_name,
        n_features=schema.n_features if schema else 0,
        n_categorical_features=len(schema.categorical_features) if schema else 0,
        data_n_rows=dataset.n_rows if dataset else 0,
        data_n_columns=dataset.n_columns if dataset else 0,
        target_column=dataset.target_column if dataset else "",
        protected_columns=dataset.protected_columns if dataset else [],
        protected_in_model=validation.protected_in_model,
        schema_passed=validation.passed,
        schema_issues=validation.issues,
        run_id=now.strftime("bias_audit_%Y%m%dT%H%M%SZ"),
        generated_at_utc=now.isoformat(),
        xgboost_version=xgb.__version__,
        python_version=sys.version,
        limitations=list(FAIRNESS_LIMITATIONS),
    )


def run_audit(
    model_path: Path,
    audit_path: Path,
    audit_name: str,
    valid_path: Path | None = None,
    threshold_request: ThresholdRequest | None = None,
    sensitive_features: str | None = None,
    audit_id: str | None = None,
    include_report_meta: bool = False,
    report_source: AuditInputSource | None = None,
) -> AuditRunResponse:
    """감사 한 건을 처음부터 끝까지 실행한다.

    검증 → 채점 → 공정성 지표 순으로 실행하고 결과를 조립한다. 검증 BLOCK 이면
    `ValidationBlockedError` 를 올린다. `include_report_meta=True` 면 편향 리포트용
    메타·증적(`report_meta`)을 함께 채운다.
    """
    validation = validate_audit_inputs(model_path, audit_path, valid_path)
    if not validation.passed:
        raise ValidationBlockedError(validation.issues)

    usable_valid_path = valid_path if validation.valid_dataset_usable else None
    audit_frame, validation_frame = load_audit_frames(audit_path, usable_valid_path)

    scoring = score_audit_dataset(
        model_path,
        validation.model_schema_info,
        audit_frame,
        threshold_config=build_threshold_config(threshold_request),
        validation_frame=validation_frame,
    )

    defaults = actual_defaults(audit_frame)
    fairness = compute_fairness_metrics(
        defaults,
        scoring.approved,
        audit_frame,
        attributes=_parse_sensitive_features(sensitive_features),
        risk_scores=scoring.risk_scores,
    )
    performance = compute_performance(defaults, scoring.risk_scores, scoring.approved)

    warnings = [
        issue
        for issue in validation.issues
        if issue.level in (IssueLevel.WARN, IssueLevel.INFO)
    ]

    return AuditRunResponse(
        audit_id=audit_id or str(uuid.uuid4()),
        audit_name=audit_name,
        threshold=scoring.threshold,
        n_customers=scoring.summary.n_customers,
        approval_rate=scoring.summary.approval_rate,
        calibration_source=scoring.calibration_source,
        performance=performance,
        fairness_by_attribute=fairness.attributes,
        fairness_summary=_summarize_fairness(fairness.attributes),
        warnings=warnings,
        report_meta=(
            _build_report_meta(
                validation, model_path, audit_path, valid_path, report_source
            )
            if include_report_meta
            else None
        ),
    )