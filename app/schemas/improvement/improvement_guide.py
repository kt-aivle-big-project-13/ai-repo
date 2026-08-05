"""개선 권고 가이드 생성 요청·응답 스키마.

규제준수 판정서와 마찬가지로 S3 의 모델·데이터를 읽지 않는다. 권고 근거는 백엔드에
이미 임계값 판정까지 끝나 저장돼 있는 값이므로, 백엔드가 조치가 필요한 항목만 골라
요청에 담아 보낸다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.report.report_narrative import ReportNarrative

Priority = Literal["HIGH", "MEDIUM", "LOW"]


class ComplianceGap(BaseModel):
    """미준수로 판정된 법령 조항."""

    law_name: str = Field(min_length=1)
    article_no: str = Field(min_length=1)
    summary: str | None = None
    evidence: str = ""


class SelfCheckGap(BaseModel):
    """자가점검에서 '아니오'로 답한 항목."""

    item_code: str = Field(min_length=1)
    label: str = Field(min_length=1)


class MetricFinding(BaseModel):
    """임계값을 넘어 조치가 필요한 지표.

    공정성은 보호속성별로 산출되므로 `attribute` 가 있고, 설명가능성은 모델 전체
    단위라 비어 있다.
    """

    attribute: str | None = None
    metric_code: str = Field(min_length=1)
    value: float | None = None
    threshold: float | None = None
    status: str = Field(min_length=1)


class ImprovementGuideRequest(BaseModel):
    """백엔드에서 전달받는 개선 권고 가이드 생성 요청."""

    audit_id: int
    audit_name: str = Field(min_length=1)
    model_name: str | None = None
    compliance_gaps: list[ComplianceGap] = Field(default_factory=list)
    self_check_gaps: list[SelfCheckGap] = Field(default_factory=list)
    # 노력의무 문항(예: 영향평가, 사전 검·인증)에서 '아니오'로 답한 것들. self_check_gaps와
    # 달리 위반이 아니라 권장 사항이라, 우선순위 과제 목록(actions)엔 안 들어가고 별도
    # 참고 권고 섹션으로만 서술된다.
    self_check_recommendations: list[SelfCheckGap] = Field(default_factory=list)
    fairness_findings: list[MetricFinding] = Field(default_factory=list)
    explainability_findings: list[MetricFinding] = Field(default_factory=list)


class ImprovementGuideResponse(BaseModel):
    """생성된 개선 권고 가이드 산출물 참조 (HTML·PDF·Word)."""

    audit_id: int
    report_s3_key: str
    pdf_report_s3_key: str
    word_report_s3_key: str
    format: Literal["html"]
    high_priority_count: int
    medium_priority_count: int
    low_priority_count: int
    generated_at: str

    # 챗봇이 리포트와 일관된 설명을 하도록 섹션별 서술을 함께 돌려준다.
    # 이미 생성한 값이라 추가 LLM 호출은 없다.
    narratives: list[ReportNarrative] = Field(default_factory=list)
