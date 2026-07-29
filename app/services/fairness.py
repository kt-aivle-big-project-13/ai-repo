"""Fairlearn 공정성 지표 계산 (이슈 #6, Proportional/FDR/FOR/FPR Parity 추가).

실제 연체 여부와 모델 예측(승인·거절)을 성별·연령대 집단별로 비교해 지표를
계산한다:

- Demographic Parity Difference — 집단 간 승인율 최대 차이
- Equal Opportunity Difference — 정상 고객(연체 안 함)의 집단 간 승인율 최대 차이
- Equalized Odds Difference — 정상 고객 오거절률 차이와 연체 고객 오승인률 차이 중 큰 값
- Proportional Parity Ratio(Disparate Impact) — 집단별 승인율 min/max 비율.
  Fairlearn 에는 없는 지표라 group_stats 의 승인율을 직접 비교해서 구한다.
  다른 지표와 달리 0이 아닌 1에 가까울수록 공정하다(0.8 이상이면 "80% Rule" 충족).
- FPR/FDR/FOR/FNR Parity — 예측 결과·실제 라벨을 분모로 삼는 조건부 지표라
  Fairlearn 에 없어 confusion matrix(TP/FP/TN/FN)를 직접 계산한다. 넷 다 집단 간
  최대-최소 격차이며 0에 가까울수록 공정하다.
  - FPR(False Positive Rate) = FP/(FP+TN) — 실제 연체 고객 중 오승인 비율
  - FDR(False Discovery Rate) = FP/(FP+TP) — 승인된 고객 중 오승인(실제 연체) 비율
  - FOR(False Omission Rate) = FN/(FN+TN) — 거절된 고객 중 오거절(실제 정상) 비율
  - FNR(False Negative Rate) = FN/(FN+TP) — 실제 정상 고객 중 오거절 비율

이 도메인은 라벨이 1=연체, 예측 1=거절로 되어 있다. Fairlearn 지표는 양성(1)을
"유리한 결과"로 보고 계산하므로, 그대로 넣으면 거절·연체 관점의 값이 나와 의미가
어긋난다. 그래서 favorable(승인=1, 정상=1) 관점으로 뒤집어 넣는다 — 이러면 지표들이
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
from app.services.performance import group_auc
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


def _confusion_counts(
    actual_favorable: np.ndarray, predicted_favorable: np.ndarray
) -> tuple[int, int, int, int]:
    """actual/predicted favorable(승인=1) 불리언 배열에서 TP/FP/TN/FN 개수를 센다."""
    tp = int((actual_favorable & predicted_favorable).sum())
    fp = int((~actual_favorable & predicted_favorable).sum())
    tn = int((~actual_favorable & ~predicted_favorable).sum())
    fn = int((actual_favorable & ~predicted_favorable).sum())
    return tp, fp, tn, fn


def _safe_rate(numerator: int, denominator: int) -> float | None:
    """분모가 0이면(해당 집단에 그 조건의 표본이 아예 없음) 정의되지 않은 값으로 None."""
    return numerator / denominator if denominator > 0 else None


def _max_min_gap(values: list[float | None]) -> float | None:
    """정의된(None 아닌) 값들의 최대-최소 격차. Fairlearn 의 *_difference 와 같은 정의.

    정의된 값이 2개 미만이면 비교 자체가 불가능하므로 None.
    """
    defined = [value for value in values if value is not None]
    if len(defined) < 2:
        return None
    return round(max(defined) - min(defined), 4)


def compute_attribute_fairness(
    actual_defaults: np.ndarray,
    approved: np.ndarray,
    group_series: pd.Series,
    attribute: str,
    risk_scores: np.ndarray | None = None,
    min_group_size: int = MIN_GROUP_SIZE,
) -> AttributeFairness:
    """보호속성 하나에 대한 공정성 지표를 계산한다.

    표본이 `min_group_size` 미만인 집단은 계산에서 제외하고, 비교 가능한 집단이
    2개 미만이면 insufficient_data 로 표시한다. `risk_scores` 를 주면 집단별 AUC 도
    함께 구한다(성능-공정성 비교용).

    FPR/FDR/FOR/FNR Parity 는 집단별 confusion matrix 에서 파생한다. 한 집단이라도
    분모가 0이면(예: 그 집단에 연체 고객이 아예 없음) 그 집단에서는 해당 지표가
    정의되지 않아 격차 계산에서 제외한다.
    """
    defaults = np.asarray(actual_defaults, dtype=int)
    approved_bool = np.asarray(approved, dtype=bool)
    groups = pd.Series(group_series).reset_index(drop=True)
    scores = None if risk_scores is None else np.asarray(risk_scores, dtype=float)

    present = groups.notna().to_numpy()
    defaults, approved_bool, groups = (
        defaults[present],
        approved_bool[present],
        groups[present].reset_index(drop=True),
    )
    if scores is not None:
        scores = scores[present]

    counts = groups.value_counts()
    kept = [str(group) for group, n in counts.items() if n >= min_group_size]
    excluded = [str(group) for group, n in counts.items() if n < min_group_size]

    group_stats: list[GroupStat] = []
    raw_approval_rates: list[float] = []
    fprs: list[float | None] = []
    fdrs: list[float | None] = []
    fors: list[float | None] = []
    fnrs: list[float | None] = []
    for group in kept:
        mask = (groups.astype(str) == group).to_numpy()
        raw_rate = float(approved_bool[mask].mean())
        raw_approval_rates.append(raw_rate)

        # favorable(승인=유리) 관점 confusion matrix — 정상 승인=TP, 연체 승인=FP,
        # 연체 거절=TN, 정상 거절=FN. 집단별 Parity 격차와 부록 근거에 함께 쓴다.
        actual_favorable = defaults[mask] == 0
        predicted_favorable = approved_bool[mask]
        tp, fp, tn, fn = _confusion_counts(actual_favorable, predicted_favorable)

        fprs.append(_safe_rate(fp, fp + tn))
        fdrs.append(_safe_rate(fp, fp + tp))
        fors.append(_safe_rate(fn, fn + tn))
        fnrs.append(_safe_rate(fn, fn + tp))

        group_stats.append(
            GroupStat(
                group=group,
                n=int(mask.sum()),
                approval_rate=round(raw_rate, 4),
                actual_default_rate=round(float(defaults[mask].mean()), 4),
                tp=tp,
                fp=fp,
                tn=tn,
                fn=fn,
                auc=None if scores is None else group_auc(defaults[mask], scores[mask]),
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

    # 그룹별 confusion matrix 에서 이미 구해둔 조건부 지표를 격차로 집계한다.
    fpr_parity = _max_min_gap(fprs)
    fdr_parity = _max_min_gap(fdrs)
    for_parity = _max_min_gap(fors)
    fnr_parity = _max_min_gap(fnrs)

    note = None
    if None in (eo, eodds, fpr_parity, fdr_parity, for_parity, fnr_parity):
        note = "일부 집단에 정상 또는 연체 고객이 없어 해당 지표를 계산할 수 없음"

    return AttributeFairness(
        attribute=attribute,
        status=FairnessStatus.COMPUTED,
        demographic_parity_difference=dp,
        equal_opportunity_difference=eo,
        equalized_odds_difference=eodds,
        proportional_parity_ratio=proportional_parity,
        fpr_parity_difference=fpr_parity,
        fdr_parity_difference=fdr_parity,
        for_parity_difference=for_parity,
        fnr_parity_difference=fnr_parity,
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
    risk_scores: np.ndarray | None = None,
) -> FairnessResult:
    """전달받은 보호속성마다 같은 지표 계산을 적용한다.

    `attributes` 를 주지 않으면 감사 데이터의 성별·연령대 컬럼을 쓴다. 컬럼이
    없는 보호속성은 insufficient_data 로 표시한다. `risk_scores` 를 주면 집단별
    AUC 도 함께 구한다.
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
            risk_scores=risk_scores,
            min_group_size=min_group_size,
        )

    return FairnessResult(attributes=results)