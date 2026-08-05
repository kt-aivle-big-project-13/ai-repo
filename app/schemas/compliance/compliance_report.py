"""규제준수 판정서 생성 요청·응답 스키마.

편향·설명가능성 리포트와 달리 S3 의 모델·데이터를 다시 읽지 않는다. 판정 근거인
자가점검 응답·법령 매핑과 참고용 감사 요약이 모두 백엔드 DB 에 이미 있으므로,
백엔드가 요청에 담아 보내고 AI 는 조판과 서술만 담당한다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.report.report_narrative import ReportNarrative


class SelfCheckAnswer(BaseModel):
    """자가점검 항목 하나에 대한 응답. 판정의 1차 근거다."""

    item_code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    answer: bool


class RegulationMapping(BaseModel):
    """자가점검 응답으로 매핑된 법령 조항과 그 준수 판정."""

    law_name: str = Field(min_length=1)
    article_no: str = Field(min_length=1)
    content: str = ""
    summary: str | None = None
    compliance: Literal["COMPLIANT", "NON_COMPLIANT", "PENDING"]
    evidence: str = ""
    effective_date: str | None = None
    revision_date: str | None = None


class FairnessReference(BaseModel):
    """참고용 공정성 지표. 판정 근거가 아니라 맥락 제공용이다."""

    attribute: str = Field(min_length=1)
    demographic_parity_difference: float | None = None
    equal_opportunity_difference: float | None = None
    equalized_odds_difference: float | None = None
    proportional_parity_ratio: float | None = None


class AuditReference(BaseModel):
    """참고용 감사 요약. 마찬가지로 판정 근거가 아니다."""

    n_customers: int | None = None
    approval_rate: float | None = None
    threshold: float | None = None
    threshold_method: str | None = None
    auc: float | None = None
    accuracy: float | None = None
    fairness: list[FairnessReference] = Field(default_factory=list)


class ComplianceReportRequest(BaseModel):
    """백엔드에서 전달받는 규제준수 판정서 생성 요청."""

    audit_id: int
    audit_name: str = Field(min_length=1)
    model_name: str | None = None
    self_check_answers: list[SelfCheckAnswer] = Field(min_length=1)
    regulation_mappings: list[RegulationMapping] = Field(min_length=1)
    audit_reference: AuditReference | None = None


class ComplianceReportResponse(BaseModel):
    """생성된 규제준수 판정서 산출물 참조 (HTML·PDF·Word)."""

    audit_id: int
    report_s3_key: str
    pdf_report_s3_key: str
    word_report_s3_key: str
    format: Literal["html"]
    compliant_count: int
    non_compliant_count: int
    pending_count: int
    generated_at: str

    # 챗봇이 리포트와 일관된 설명을 하도록 섹션별 서술을 함께 돌려준다.
    # 이미 생성한 값이라 추가 LLM 호출은 없다.
    narratives: list[ReportNarrative] = Field(default_factory=list)
