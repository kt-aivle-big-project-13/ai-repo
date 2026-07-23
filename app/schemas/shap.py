"""SHAP 설명가능성 분석 요청·응답 스키마."""

from pydantic import BaseModel, Field


class ShapAnalysisRequest(BaseModel):
    """백엔드에서 전달받는 SHAP 분석 요청."""

    audit_id: int
    model_s3_key: str = Field(min_length=1)
    audit_dataset_s3_key: str = Field(min_length=1)
    target_column: str = Field(min_length=1)
    sensitive_features: list[str] = Field(min_length=1)


class ShapMetricResult(BaseModel):
    """SHAP 지표 한 건."""

    metric: str
    label: str
    value: float
    threshold: float
    status: str


class ShapKeyMetrics(BaseModel):
    """백엔드가 저장하는 세 가지 핵심 SHAP 지표."""

    sensitive_contribution_ratio: ShapMetricResult
    global_explanation_stability: ShapMetricResult
    explanation_fidelity: ShapMetricResult


class ShapAnalysisResponse(BaseModel):
    """백엔드 ExplainabilityResultRequest와 동일한 응답."""

    pipeline_status: str
    overall_status: str
    key_metrics: ShapKeyMetrics
