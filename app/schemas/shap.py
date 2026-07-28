"""SHAP 설명가능성 분석 요청·응답 스키마."""

from typing import Literal

from pydantic import BaseModel, Field

ShapStatus = Literal[
    "PASS",
    "WARNING",
    "REVIEW",
    "NOT_EVALUATED",
]

ArtifactsStatus = Literal[
    "UPLOADED",
    "UPLOAD_FAILED",
    "SKIPPED",
]


class ShapAnalysisRequest(BaseModel):
    """백엔드에서 전달받는 SHAP 분석 요청."""

    audit_id: int
    model_s3_key: str = Field(min_length=1)
    audit_dataset_s3_key: str = Field(min_length=1)
    target_column: str = Field(min_length=1)
    sensitive_features: list[str] = Field(min_length=1)
    # 리포트 확장 (기본 off → 기존 동작·계약 유지)
    include_report: bool = False
    report_top_n: int = Field(default=20, ge=1, le=200)


class ShapMetricResult(BaseModel):
    """SHAP 지표 한 건."""

    metric: str
    label: str
    value: float | None
    threshold: float
    review_threshold: float
    status: ShapStatus


class ShapKeyMetrics(BaseModel):
    """백엔드가 저장하는 세 가지 핵심 SHAP 지표."""

    sensitive_contribution_ratio: ShapMetricResult
    global_explanation_stability: ShapMetricResult
    explanation_fidelity: ShapMetricResult


class FeatureImportance(BaseModel):
    """전역 변수 중요도 한 줄."""

    rank: int
    feature: str
    mean_abs_shap: float
    mean_signed_shap: float
    contribution_ratio: float | None
    direction: Literal["RISK_INCREASE", "RISK_DECREASE"]
    is_sensitive: bool
    sensitive_group: str | None = None


class MetricDetail(BaseModel):
    """지표별 측정값·기준·상태와 부가 수치."""

    metric: str
    value: float | None
    threshold: float | None
    review_threshold: float | None
    status: ShapStatus
    extra: dict[str, float] = Field(default_factory=dict)


class SchemaValidation(BaseModel):
    """입력 스키마 검증 결과."""

    status: str
    row_count: int
    model_feature_count: int
    dataset_column_count: int
    missing_model_features: list[str]
    missing_required_columns: list[str]
    duplicate_columns: list[str]
    audit_only_columns: list[str]


class ShapReport(BaseModel):
    """LLM 리포트 생성을 위한 구조화 데이터 (인라인)."""

    schema_validation: SchemaValidation
    metrics: list[MetricDetail]
    global_importance_top: list[FeatureImportance]
    sampling: dict[str, float]
    thresholds: dict[str, float]
    manifest: dict[str, str]
    limitations: list[str]


class ArtifactFile(BaseModel):
    """S3에 저장된 산출물 한 건."""

    name: str
    s3_key: str
    kind: Literal["table", "figure", "json", "other"]


class ShapArtifacts(BaseModel):
    """S3에 영속화된 증적 참조."""

    bucket: str
    prefix: str
    files: list[ArtifactFile]


class ShapAnalysisResponse(BaseModel):
    """백엔드 ExplainabilityResultRequest와 동일한 응답.

    `report`·`artifacts`·`artifacts_status` 는 `include_report=True` 일 때만
    채워지는 확장 필드로, 라우터에서 exclude_unset 직렬화해 기존 계약을 지킨다.
    """

    pipeline_status: str
    overall_status: ShapStatus
    key_metrics: ShapKeyMetrics
    report: ShapReport | None = None
    artifacts: ShapArtifacts | None = None
    artifacts_status: ArtifactsStatus | None = None
