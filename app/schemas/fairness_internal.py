"""공정성 감사 내부 연동(S3 기반) 요청 스키마.

백엔드(Spring)가 S3에 저장한 파일의 Key를 넘겨 공정성 감사를 요청할 때 쓴다.
필드명은 백엔드 `FairnessAnalysisRequest`(snake_case)와 1:1로 맞춘다.
"""

from pydantic import BaseModel, Field


class FairnessAnalyzeRequest(BaseModel):
    """백엔드에서 전달받는 공정성 분석 요청 (S3 Key 기반)."""

    audit_id: int
    model_s3_key: str = Field(min_length=1)
    audit_dataset_s3_key: str = Field(min_length=1)
    validation_dataset_s3_key: str | None = None
    audit_name: str = Field(min_length=1)
    target_approval_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    manual_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    sensitive_features: list[str] = Field(min_length=1)
