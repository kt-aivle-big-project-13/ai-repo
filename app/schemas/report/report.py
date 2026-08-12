"""설명가능성 리포트 생성 요청·응답 스키마."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.report.report_narrative import ReportNarrative


class ReportRequest(BaseModel):
    """백엔드에서 전달받는 리포트 생성 요청 (S3 Key 기반).

    모델·데이터셋 Key 는 직전 SHAP 분석 산출물을 찾지 못했을 때 분석을 직접
    실행하는 폴백 경로에서만 쓰인다. 평상시에는 `/internal/v1/shap/analyze` 가
    S3 에 남긴 산출물을 재사용하므로 파이프라인을 다시 돌리지 않는다.
    """

    audit_id: int
    model_s3_key: str = Field(min_length=1)
    audit_dataset_s3_key: str = Field(min_length=1)
    target_column: str = Field(min_length=1)
    sensitive_features: list[str] = Field(min_length=1)
    report_top_n: int = Field(default=20, ge=1, le=200)

    # 재사용할 SHAP 산출물 프리픽스(`explainability/{audit_id}/{run_id}`). 백엔드가
    # 분석 응답의 artifacts.prefix 를 저장해 넘겨주면 그대로 쓰고, 없으면 audit_id
    # 아래에서 가장 최근 실행을 찾는다.
    analysis_prefix: str | None = None


class ReportResponse(BaseModel):
    """생성된 HTML·PDF 리포트 산출물 참조."""

    audit_id: int
    report_s3_key: str
    pdf_report_s3_key: str
    word_report_s3_key: str
    format: Literal["html"]
    overall_status: str
    generated_at: str

    # 챗봇이 리포트와 일관된 설명을 하도록 섹션별 서술을 함께 돌려준다.
    # 이미 생성한 값이라 추가 LLM 호출은 없다.
    narratives: list[ReportNarrative] = Field(default_factory=list)
