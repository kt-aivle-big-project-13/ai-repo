"""감사 실행 오케스트레이션 (이슈 #7).

검증(#4) → 위험점수·임계값(#5) → 공정성 지표(#6)를 순서대로 호출해 하나의 감사
결과로 조립한다. 각 단계는 앞 단계의 산출물을 그대로 받아 쓰므로 같은 계산을
반복하지 않는다.

검증에서 BLOCK 이 나오면 이후 단계를 실행하지 않고 `ValidationBlockedError` 를
올린다 — 서버 오류와 구분해 API 가 다른 상태 코드로 응답하기 위함이다.
"""

import uuid
from pathlib import Path

from app.schemas.audit import AuditRunResponse, FairnessMetricValues, ThresholdRequest
from app.schemas.fairness import AttributeFairness
from app.schemas.scoring import ThresholdConfig, ThresholdMethod
from app.schemas.validation import IssueLevel, ValidationIssue
from app.services.fairness import compute_fairness_metrics
from app.services.performance import compute_performance
from app.services.scoring import actual_defaults, load_audit_frames, score_audit_dataset
from app.services.validation import validate_audit_inputs


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


def run_audit(
    model_path: Path,
    audit_path: Path,
    audit_name: str,
    valid_path: Path | None = None,
    threshold_request: ThresholdRequest | None = None,
    sensitive_features: str | None = None,
    audit_id: str | None = None,
) -> AuditRunResponse:
    """감사 한 건을 처음부터 끝까지 실행한다.

    검증 → 채점 → 공정성 지표 순으로 실행하고 결과를 조립한다. 검증 BLOCK 이면
    `ValidationBlockedError` 를 올린다.
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
    )