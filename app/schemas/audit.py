"""감사 실행 API 요청·응답 스키마 (이슈 #7)."""

from pydantic import BaseModel, Field

from app.schemas.fairness import AttributeFairness
from app.schemas.performance import PerformanceSummary
from app.schemas.scoring import CalibrationSource, ThresholdInfo
from app.schemas.validation import ValidationIssue


class ThresholdRequest(BaseModel):
    """임계값 설정 요청.

    백엔드 `AuditUploadRequest` 와 맞춘다. 둘 다 선택이며, `threshold`(수동값)가
    있으면 그것을 우선 쓰고, 없으면 `target_approval_rate` 로 산출한다. 둘 다
    없으면 기본 목표 승인율(0.90)을 쓴다.
    """

    target_approval_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    threshold: float | None = Field(default=None, gt=0.0, le=1.0)


class FairnessMetricValues(BaseModel):
    """보호속성 하나의 지표를 백엔드 FairnessMetricCode 키로 담는다."""

    DEMOGRAPHIC_PARITY: float | None
    EQUAL_OPPORTUNITY: float | None
    EQUALIZED_ODDS: float | None
    PROPORTIONAL_PARITY: float | None
    FPR_PARITY: float | None
    FDR_PARITY: float | None
    FOR_PARITY: float | None


class AuditReportMeta(BaseModel):
    """편향 리포트용 메타·증적.

    공정성 지표 외에, 검증 단계(#4)가 이미 계산해 두고도 응답에 담기지 않던
    모델·데이터 정보, 스키마 검증 결과, 재현성 정보를 리포트가 쓸 수 있게 surface
    한다. `include_report_meta=True` 일 때만 채워진다.
    """

    # 모델·데이터 정보 (리포트 2장)
    model_file: str
    n_features: int
    n_categorical_features: int
    data_n_rows: int
    data_n_columns: int
    target_column: str
    protected_columns: list[str]
    protected_in_model: dict[str, bool]

    # 스키마 검증 (리포트 3장)
    schema_passed: bool
    schema_issues: list[ValidationIssue]

    # 증적·재현성 (리포트 6장)
    run_id: str
    generated_at_utc: str
    xgboost_version: str
    python_version: str

    # 한계 (리포트 2장)
    limitations: list[str]


class AuditRunResponse(BaseModel):
    """감사 실행 결과.

    `threshold` 는 승인·거절에 쓴 임계값이고, `fairness_by_attribute` 는 성별·
    연령대 등 보호속성별 공정성 지표다. 공정성 지표의 NORMAL/REVIEW 판정은 정책
    영역이라 여기서 내리지 않고 raw 값만 반환한다.
    """

    audit_id: str = Field(description="이 감사 실행의 식별자 (AI 서버 생성 UUID)")
    audit_name: str

    threshold: ThresholdInfo
    n_customers: int
    approval_rate: float
    calibration_source: CalibrationSource

    performance: PerformanceSummary = Field(
        description="감사셋 전체의 모델 판별 성능 (AUC·정확도). 집단별 AUC 는 지표 상세에"
    )

    fairness_by_attribute: dict[str, AttributeFairness] = Field(
        description="보호속성 컬럼명 → 지표 상세"
    )
    fairness_summary: dict[str, FairnessMetricValues] = Field(
        description="보호속성별 지표를 FairnessMetricCode 키로 정리한 요약"
    )

    warnings: list[ValidationIssue] = Field(
        default_factory=list, description="검증 단계의 WARN·INFO (BLOCK 은 아님)"
    )

    report_meta: AuditReportMeta | None = Field(
        default=None,
        description="편향 리포트용 메타·증적. include_report_meta=True 일 때만 채워짐",
    )


class AuditErrorResponse(BaseModel):
    """검증 실패 응답. BLOCK 이슈 때문에 감사를 진행하지 못한 경우."""

    detail: str
    issues: list[ValidationIssue]