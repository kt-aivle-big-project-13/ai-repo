"""편향진단 리포트 생성 요청·응답 스키마."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.report_narrative import ReportNarrative


class BiasReportRequest(BaseModel):
    """백엔드에서 전달받는 편향진단 리포트 생성 요청 (S3 Key 기반).

    필드는 공정성 감사 요청(`FairnessAnalyzeRequest`)과 1:1로 맞춰, 그대로
    분석에 넘길 수 있게 한다.
    """

    audit_id: int
    model_s3_key: str = Field(min_length=1)
    audit_dataset_s3_key: str = Field(min_length=1)
    validation_dataset_s3_key: str | None = None
    audit_name: str = Field(min_length=1)
    target_approval_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    manual_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    sensitive_features: list[str] = Field(min_length=1)


class BiasReportResponse(BaseModel):
    """생성된 편향진단 리포트 산출물 참조 (HTML·PDF·Word)."""

    audit_id: int
    report_s3_key: str
    pdf_report_s3_key: str
    word_report_s3_key: str
    format: Literal["html"]
    generated_at: str

    # 챗봇이 리포트와 일관된 설명을 하도록 섹션별 서술을 함께 돌려준다.
    # 이미 생성한 값이라 추가 LLM 호출은 없다.
    narratives: list[ReportNarrative] = Field(default_factory=list)
