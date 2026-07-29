"""모델 판별 성능 지표 스키마 (편향 보고서 성능·트레이드오프 섹션용)."""

from pydantic import BaseModel, Field


class PerformanceSummary(BaseModel):
    """감사 데이터 전체에 대한 모델 판별 성능.

    공정성 지표가 집단 간 격차를 본다면, 여기서는 모델이 연체를 얼마나 잘
    가려내는지를 본다. 집단별 AUC 는 공정성 지표의 `GroupStat.auc` 에 담긴다.
    """

    auc: float | None = Field(
        default=None,
        description="위험점수 기준 ROC AUC. 감사셋에 정상·연체가 한 쪽만 있으면 None",
    )
    accuracy: float | None = Field(
        default=None,
        description="거절=연체 예측으로 보고 실제 정상·연체 라벨과 일치한 비율",
    )
    note: str | None = None
