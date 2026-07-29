"""Fairlearn 공정성 지표 스키마."""

from enum import Enum

from pydantic import BaseModel, Field


class FairnessStatus(str, Enum):
    """보호속성 하나에 대한 지표 계산 결과 상태."""

    COMPUTED = "computed"
    INSUFFICIENT_DATA = "insufficient_data"


class GroupStat(BaseModel):
    """집단(예: 성별 M/F) 하나의 기초 통계와 혼동행렬.

    혼동행렬은 favorable(승인=유리) 관점이다: 정상 고객을 승인하면 TP, 연체
    고객을 승인하면 FP(오승인), 연체 고객을 거절하면 TN, 정상 고객을 거절하면
    FN(오거절)이다. FPR/FDR/FOR/FNR Parity 는 이 값에서 파생된다.
    """

    group: str
    n: int
    approval_rate: float = Field(description="승인 고객 수 / 집단 인원")
    actual_default_rate: float = Field(description="실제 연체 고객 수 / 집단 인원")
    tp: int = Field(description="정상 고객을 승인한 수 (올바른 승인)")
    fp: int = Field(description="연체 고객을 승인한 수 (오승인)")
    tn: int = Field(description="연체 고객을 거절한 수 (올바른 거절)")
    fn: int = Field(description="정상 고객을 거절한 수 (오거절)")
    auc: float | None = Field(
        default=None,
        description="집단 내 위험점수의 ROC AUC. 한 클래스만 있거나 점수가 없으면 None",
    )


class AttributeFairness(BaseModel):
    """보호속성(성별·연령대 등) 하나의 공정성 지표.

    favorable(승인=유리) 관점으로 계산하며, proportional_parity_ratio 를 제외한
    나머지는 모두 집단 간 최대 격차(difference)로 0에 가까울수록 공정하다.
    proportional_parity_ratio 만 비율(ratio)이라 1에 가까울수록 공정하다. 표본이
    부족해 계산할 수 없으면 status 가 insufficient_data 이고 지표는 모두 None 이다.
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
    proportional_parity_ratio: float | None = Field(
        default=None,
        description=(
            "Proportional Parity(Disparate Impact) — 집단별 승인율 중 "
            "최솟값/최댓값 비율. 0.8 이상이면 80% Rule 충족(다른 세 지표와 달리 "
            "0이 아닌 1에 가까울수록 공정)"
        ),
    )
    fpr_parity_difference: float | None = Field(
        default=None,
        description="FPR(FP/(FP+TN)) 집단 간 최대-최소 격차 — 실제 연체 고객 중 오승인 비율",
    )
    fdr_parity_difference: float | None = Field(
        default=None,
        description="FDR(FP/(FP+TP)) 집단 간 최대-최소 격차 — 승인된 고객 중 오승인 비율",
    )
    for_parity_difference: float | None = Field(
        default=None,
        description="FOR(FN/(FN+TN)) 집단 간 최대-최소 격차 — 거절된 고객 중 오거절 비율",
    )
    fnr_parity_difference: float | None = Field(
        default=None,
        description="FNR(FN/(FN+TP)) 집단 간 최대-최소 격차 — 실제 정상 고객 중 오거절 비율",
    )
    groups: list[GroupStat] = Field(default_factory=list)
    excluded_groups: list[str] = Field(
        default_factory=list, description="표본 부족으로 계산에서 제외된 집단"
    )
    note: str | None = None


class FairnessResult(BaseModel):
    """전체 공정성 지표 결과. 보호속성별로 지표를 담는다."""

    attributes: dict[str, AttributeFairness]