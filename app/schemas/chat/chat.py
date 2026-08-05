"""감사 질의 응답 요청·응답 스키마.

백엔드가 권한 확인과 근거 조립을 마친 뒤 질문과 함께 보낸다. AI 서버는 계산하지도
판정하지도 않고, 주입된 근거만으로 답한다.

기획은 model-repo `docs/04-chatbot-plan.md` 참고.
"""

from typing import Literal

from pydantic import BaseModel, Field

CitationType = Literal[
    "AUDIT_METRIC",
    "GROUP_STAT",
    "LAW_ARTICLE",
    "REPORT_SECTION",
]

GroundingStatus = Literal["GROUNDED", "PARTIAL", "NOT_GROUNDED"]


class AuditFact(BaseModel):
    """저장된 감사 수치 하나. 지표·집단통계·SHAP·자가점검을 모두 담는다."""

    kind: Literal["AUDIT_METRIC", "GROUP_STAT"] = "AUDIT_METRIC"
    reference: str = Field(min_length=1, description="예: EQUAL_OPPORTUNITY / AGE_GROUP")
    value: str | None = None
    detail: str | None = Field(
        default=None, description="임계값·상태 등 값 해석에 필요한 부가 정보"
    )


class LawArticle(BaseModel):
    """pgvector 검색으로 찾은 관련 법령 조항 (2단계에서 채워진다)."""

    law_name: str = Field(min_length=1)
    article_no: str = Field(min_length=1)
    summary: str | None = None
    content: str = ""
    similarity: float | None = None
    source_url: str | None = None
    revised: bool = Field(
        default=False, description="개정 이력이 있는 조항이면 답변에 주의 표시"
    )


class ReportSection(BaseModel):
    """생성된 리포트의 섹션별 서술 (3단계에서 채워진다)."""

    report_type: str = Field(min_length=1, description="예: BIAS_REPORT")
    section_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ChatAnswerRequest(BaseModel):
    """백엔드에서 전달받는 질의 응답 생성 요청.

    세 근거는 모두 선택 항목이다. 단계적으로 채워지지만 계약은 처음부터 확정한다.
    """

    audit_id: int
    question: str = Field(min_length=1, max_length=2000)
    audit_facts: list[AuditFact] = Field(default_factory=list)
    law_articles: list[LawArticle] = Field(default_factory=list)
    report_sections: list[ReportSection] = Field(default_factory=list)


class Citation(BaseModel):
    """답변이 실제로 인용한 근거."""

    type: CitationType
    reference: str
    value: str | None = None
    similarity: float | None = None
    source_url: str | None = None
    revised: bool = False


class ChatAnswerResponse(BaseModel):
    """생성된 답변과 그 근거."""

    audit_id: int
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    grounding_status: GroundingStatus
    generated_at: str
