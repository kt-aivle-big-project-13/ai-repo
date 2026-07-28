"""Fairlearn 공정성 지표 3개 계산 (이슈 #6).

실제 연체 여부와 모델 예측(승인·거절)을 성별·연령대 집단별로 비교해 지표 3개를
계산한다:

- Demographic Parity Difference — 집단 간 승인율 최대 차이
- Equal Opportunity Difference — 정상 고객(연체 안 함)의 집단 간 승인율 최대 차이
- Equalized Odds Difference — 정상 고객 오거절률 차이와 연체 고객 오승인률 차이 중 큰 값

이 도메인은 라벨이 1=연체, 예측 1=거절로 되어 있다. Fairlearn 지표는 양성(1)을
"유리한 결과"로 보고 계산하므로, 그대로 넣으면 거절·연체 관점의 값이 나와 의미가
어긋난다. 그래서 favorable(승인=1, 정상=1) 관점으로 뒤집어 넣는다 — 이러면 세 지표가
위 정의와 정확히 일치하고, 여신 심사의 표준 관례(양성 = 승인)와도 맞는다.
"""

import numpy as np
import pandas as pd
from fairlearn.metrics import (
    demographic_parity_difference,
    equal_opportunity_difference,
    equalized_odds_difference,
)

from app.schemas.fairness import (
    AttributeFairness,
    FairnessResult,
    FairnessStatus,
    GroupStat,
)
from app.services.validation import (
    AGE_GROUP_COLUMN,
    GENDER_COLUMN,
    MIN_GROUP_SIZE,
)

# 감사 데이터에서 공정성 지표를 계산할 보호속성 컬럼.
DEFAULT_PROTECTED_ATTRIBUTES = (GENDER_COLUMN, AGE_GROUP_COLUMN)


def _to_float(value: float) -> float | None:
    """Fairlearn 이 돌려준 값을 API 응답용 float 로 바꾼다. NaN 은 None 으로."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return round(float(value), 4)


def compute_attribute_fairness(
    actual_defaults: np.ndarray,
    approved: np.ndarray,
    group_series: pd.Series,
    attribute: str,
    min_group_size: int = MIN_GROUP_SIZE,
) -> AttributeFairness:
    """보호속성 하나에 대한 공정성 지표를 계산한다.

    표본이 `min_group_size` 미만인 집단은 계산에서 제외하고, 비교 가능한 집단이
    2개 미만이면 insufficient_data 로 표시한다.
    """
    defaults = np.asarray(actual_defaults, dtype=int)
    approved_bool = np.asarray(approved, dtype=bool)
    groups = pd.Series(group_series).reset_index(drop=True)

    present = groups.notna().to_numpy()
    defaults, approved_bool, groups = (
        defaults[present],
        approved_bool[present],
        groups[present].reset_index(drop=True),
    )

    counts = groups.value_counts()
    kept = [str(group) for group, n in counts.items() if n >= min_group_size]
    excluded = [str(group) for group, n in counts.items() if n < min_group_size]

    group_stats: list[GroupStat] = []
    raw_approval_rates: list[float] = []
    for group in kept:
        mask = (groups.astype(str) == group).to_numpy()
        raw_rate = float(approved_bool[mask].mean())
        raw_approval_rates.append(raw_rate)
        group_stats.append(
            GroupStat(
                group=group,
                n=int(mask.sum()),
                approval_rate=round(raw_rate, 4),
                actual_default_rate=round(float(defaults[mask].mean()), 4),
            )
        )

    if len(kept) < 2:
        return AttributeFairness(
            attribute=attribute,
            status=FairnessStatus.INSUFFICIENT_DATA,
            groups=group_stats,
            excluded_groups=excluded,
            note="비교 가능한 충분표본 집단이 2개 미만",
        )

    # 유지된 집단의 행만 남기고 favorable 관점으로 변환한다.
    keep_mask = groups.astype(str).isin(kept).to_numpy()
    sensitive = groups.astype(str)[keep_mask].to_numpy()
    favorable_label = 1 - defaults[keep_mask]  # 1 = 정상(연체 안 함)
    favorable_pred = approved_bool[keep_mask].astype(int)  # 1 = 승인

    metric_args = dict(
        y_true=favorable_label,
        y_pred=favorable_pred,
        sensitive_features=sensitive,
    )

    dp = _to_float(demographic_parity_difference(**metric_args))
    eo = _to_float(equal_opportunity_difference(**metric_args))
    eodds = _to_float(equalized_odds_difference(**metric_args))

    # group_stats 계산 중 이미 구해둔 원시(반올림 전) 승인율을 재사용한다 — Fairlearn
    # 을 다시 부르지 않고, 반올림된 값끼리 나누는 이중 반올림 오차도 피한다.
    max_rate = max(raw_approval_rates)
    proportional_parity = (
        round(min(raw_approval_rates) / max_rate, 4) if max_rate > 0 else None
    )
    note = None
    if None in (eo, eodds):
        note = "일부 집단에 정상 또는 연체 고객이 없어 해당 지표를 계산할 수 없음"

    return AttributeFairness(
        attribute=attribute,
        status=FairnessStatus.COMPUTED,
        demographic_parity_difference=dp,
        equal_opportunity_difference=eo,
        equalized_odds_difference=eodds,
        proportional_parity_ratio=proportional_parity,
        groups=group_stats,
        excluded_groups=excluded,
        note=note,
    )


def compute_fairness_metrics(
    actual_defaults: np.ndarray,
    approved: np.ndarray,
    protected_frame: pd.DataFrame,
    attributes: list[str] | None = None,
    min_group_size: int = MIN_GROUP_SIZE,
) -> FairnessResult:
    """전달받은 보호속성마다 같은 지표 계산을 적용한다.

    `attributes` 를 주지 않으면 감사 데이터의 성별·연령대 컬럼을 쓴다. 컬럼이
    없는 보호속성은 insufficient_data 로 표시한다.
    """
    selected = attributes or [
        column for column in DEFAULT_PROTECTED_ATTRIBUTES if column in protected_frame.columns
    ]

    results: dict[str, AttributeFairness] = {}
    for attribute in selected:
        if attribute not in protected_frame.columns:
            results[attribute] = AttributeFairness(
                attribute=attribute,
                status=FairnessStatus.INSUFFICIENT_DATA,
                note="보호속성 컬럼이 감사 데이터에 없음",
            )
            continue
        results[attribute] = compute_attribute_fairness(
            actual_defaults,
            approved,
            protected_frame[attribute],
            attribute,
            min_group_size,
        )

    return FairnessResult(attributes=results)
