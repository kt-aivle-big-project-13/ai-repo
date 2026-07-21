"""Fairlearn 공정성 지표 스키마."""

from enum import Enum

from pydantic import BaseModel, Field


class FairnessStatus(str, Enum):
    """보호속성 하나에 대한 지표 계산 결과 상태."""

    COMPUTED = "computed"
    INSUFFICIENT_DATA = "insufficient_data"


class GroupStat(BaseModel):
    """집단(예: 성별 M/F) 하나의 기초 통계."""

    group: str
    n: int
    approval_rate: float = Field(description="승인 고객 수 / 집단 인원")
    actual_default_rate: float = Field(description="실제 연체 고객 수 / 집단 인원")


class AttributeFairness(BaseModel):
    """보호속성(성별·연령대 등) 하나의 공정성 지표.

    세 지표는 모두 favorable(승인=유리) 관점의 집단 간 최대 격차이며 0에 가까울수록
    공정하다. 표본이 부족해 계산할 수 없으면 status 가 insufficient_data 이고 지표는
    모두 None 이다.
    """

    attribute: str
    status: FairnessStatus
    demographic_parity_difference: float | None = Field(
        default=None, description="집단 간 승인율 최대 차이"
    )
    equal_opportunity_difference: float | None = Field(
        default=None, description="정상 고객의 집단 간 승인율 최대 차이"
    )
    equalized_odds_difference: float | None = Field(
        default=None,
        description="정상 고객 오거절률·연체 고객 오승인률의 집단 간 최대 차이 중 큰 값",
    )
    groups: list[GroupStat] = Field(default_factory=list)
    excluded_groups: list[str] = Field(
        default_factory=list, description="표본 부족으로 계산에서 제외된 집단"
    )
    note: str | None = None


class FairnessResult(BaseModel):
    """전체 공정성 지표 결과. 보호속성별로 지표를 담는다."""

    attributes: dict[str, AttributeFairness]
