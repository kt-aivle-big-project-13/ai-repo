"""설명가능성 리포트 생성 요청·응답 스키마."""

from typing import Literal

from pydantic import BaseModel, Field


class ReportRequest(BaseModel):
    """백엔드에서 전달받는 리포트 생성 요청 (S3 Key 기반)."""

    audit_id: int
    model_s3_key: str = Field(min_length=1)
    audit_dataset_s3_key: str = Field(min_length=1)
    target_column: str = Field(min_length=1)
    sensitive_features: list[str] = Field(min_length=1)
    report_top_n: int = Field(default=20, ge=1, le=200)


class ReportResponse(BaseModel):
    """생성된 HTML·PDF 리포트 산출물 참조."""

    audit_id: int
    report_s3_key: str
    pdf_report_s3_key: str
    word_report_s3_key: str
    format: Literal["html"]
    overall_status: str
    generated_at: str
