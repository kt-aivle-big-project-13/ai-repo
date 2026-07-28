"""Fairlearn 공정성 지표 계산 테스트."""

import numpy as np
import pandas as pd
import pytest

from app.schemas.fairness import FairnessStatus
from app.services.fairness import compute_attribute_fairness, compute_fairness_metrics


def _make_case(n=4000, male_approval=0.9, female_approval=0.8, seed=0):
    """성별에 따라 승인율이 다른 합성 감사 결과."""
    rng = np.random.default_rng(seed)
    gender = rng.choice(["M", "F"], n)
    defaults = rng.integers(0, 2, n)
    rates = np.where(gender == "M", male_approval, female_approval)
    approved = rng.random(n) < rates
    return defaults, approved, pd.Series(gender)


def test_metrics_match_manual_definition():
    """세 지표가 수동 정의(승인율 관점)와 일치한다."""
    defaults, approved, gender = _make_case()

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    def rate(mask):
        return approved[mask].mean()

    m, f = (gender == "M").to_numpy(), (gender == "F").to_numpy()
    dp_manual = abs(rate(m) - rate(f))
    gm, gf = m & (defaults == 0), f & (defaults == 0)
    eo_manual = abs(rate(gm) - rate(gf))

    assert result.status is FairnessStatus.COMPUTED
    assert result.demographic_parity_difference == pytest.approx(dp_manual, abs=0.001)
    assert result.equal_opportunity_difference == pytest.approx(eo_manual, abs=0.001)


def test_fair_case_has_small_gaps():
    """집단 간 승인율이 같으면 지표가 0에 가깝다."""
    defaults, approved, gender = _make_case(male_approval=0.85, female_approval=0.85)

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    assert result.demographic_parity_difference < 0.05
    assert result.equalized_odds_difference < 0.05
    assert result.proportional_parity_ratio > 0.95


def test_group_stats_reported():
    defaults, approved, gender = _make_case()

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    assert {g.group for g in result.groups} == {"M", "F"}
    assert all(g.n > 0 for g in result.groups)
    assert sum(g.n for g in result.groups) == len(gender)


def test_single_group_is_insufficient():
    """집단이 하나뿐이면 계산하지 않는다."""
    defaults, approved, _ = _make_case()
    gender = pd.Series(["M"] * len(defaults))

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    assert result.status is FairnessStatus.INSUFFICIENT_DATA
    assert result.demographic_parity_difference is None


def test_small_group_excluded():
    """표본이 적은 집단은 제외되고, 남은 집단이 2개 미만이면 insufficient."""
    defaults, approved, gender = _make_case(n=1000)
    gender = gender.copy()
    gender.iloc[:10] = "X"  # 10명짜리 소수 집단

    result = compute_attribute_fairness(
        defaults, approved, gender, "성별", min_group_size=300
    )

    assert "X" in result.excluded_groups


def test_insufficient_when_all_groups_too_small():
    defaults, approved, gender = _make_case(n=200)

    result = compute_attribute_fairness(
        defaults, approved, gender, "성별", min_group_size=300
    )

    assert result.status is FairnessStatus.INSUFFICIENT_DATA
    assert set(result.excluded_groups) == {"M", "F"}


def test_nan_group_values_dropped():
    """보호속성 값이 결측인 행은 계산에서 빠진다."""
    defaults, approved, gender = _make_case(n=2000)
    gender = gender.copy()
    gender.iloc[:100] = None

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    assert result.status is FairnessStatus.COMPUTED
    assert sum(g.n for g in result.groups) == len(gender) - 100


def test_compute_over_multiple_attributes():
    defaults, approved, gender = _make_case(n=3000)
    age = pd.Series(np.random.default_rng(1).choice(["30대", "40대", "50대"], len(defaults)))
    frame = pd.DataFrame({"CODE_GENDER": gender, "AGE_GROUP": age})

    result = compute_fairness_metrics(defaults, approved, frame)

    assert set(result.attributes) == {"CODE_GENDER", "AGE_GROUP"}
    assert result.attributes["CODE_GENDER"].status is FairnessStatus.COMPUTED
    assert result.attributes["AGE_GROUP"].status is FairnessStatus.COMPUTED


def test_missing_attribute_column_is_insufficient():
    defaults, approved, gender = _make_case(n=1000)
    frame = pd.DataFrame({"CODE_GENDER": gender})

    result = compute_fairness_metrics(defaults, approved, frame, attributes=["AGE_GROUP"])

    assert result.attributes["AGE_GROUP"].status is FairnessStatus.INSUFFICIENT_DATA


def test_metrics_are_json_serializable_numbers():
    """API 응답용으로 순수 float/None 이어야 한다."""
    defaults, approved, gender = _make_case()

    result = compute_attribute_fairness(defaults, approved, gender, "성별")
    dumped = result.model_dump()

    for key in (
        "demographic_parity_difference",
        "equal_opportunity_difference",
        "equalized_odds_difference",
        "proportional_parity_ratio",
    ):
        assert dumped[key] is None or isinstance(dumped[key], float)

def test_proportional_parity_matches_manual_ratio():
    """Proportional Parity 는 원본 approved 배열로 직접 구한 승인율의 min/max 비율과 일치한다.

    구현이 반올림해서 돌려주는 result.groups 를 다시 가져다 비교하면 구현의 반올림
    오차를 그대로 답으로 써버리는 순환 검증이 되므로, 원본 배열에서 별도로 계산한다.
    """
    defaults, approved, gender = _make_case(male_approval=0.9, female_approval=0.8)

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    m, f = (gender == "M").to_numpy(), (gender == "F").to_numpy()
    rate_m, rate_f = approved[m].mean(), approved[f].mean()
    manual_ratio = min(rate_m, rate_f) / max(rate_m, rate_f)

    # 구현이 최종 비율을 소수 4자리로 반올림해 돌려주므로, 그 한 번의 반올림 오차
    # (최대 5e-5)만 허용한다 — 그보다 크게 벗어나면 중간값(그룹별 승인율)을 먼저
    # 반올림한 뒤 나누는 이중 반올림 버그가 재발했다는 뜻이다.
    assert result.proportional_parity_ratio == pytest.approx(manual_ratio, abs=5e-5)

def test_proportional_parity_deterministic_boundary_case():
    """반올림에 의존하지 않는, 손으로 검산 가능한 결정론적 경계 케이스.

    A집단 500명 전원 승인(1.0), B집단 500명 중 350명 승인(0.7) → 비율 정확히 0.7로
    80% Rule 미만. 정수 카운트로 구성해 부동소수점/반올림 우연에 기대지 않는다.
    """
    n_per_group = 500
    gender = pd.Series(["A"] * n_per_group + ["B"] * n_per_group)
    approved = np.array([True] * n_per_group + [True] * 350 + [False] * 150)
    defaults = np.zeros(n_per_group * 2, dtype=int)

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    assert result.proportional_parity_ratio == pytest.approx(0.7, abs=1e-9)
    assert result.proportional_parity_ratio < 0.8

def test_proportional_parity_below_80_percent_rule():
    """승인율 격차가 크면 80% Rule(0.8) 미만으로 나온다."""
    defaults, approved, gender = _make_case(male_approval=0.9, female_approval=0.5)

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    assert result.proportional_parity_ratio < 0.8


def test_proportional_parity_none_when_no_one_approved():
    """모든 유지 집단의 승인율이 0이면 비율을 정의할 수 없어 None 이다."""
    n = 4000
    defaults = np.random.default_rng(0).integers(0, 2, n)
    gender = pd.Series(np.random.default_rng(1).choice(["M", "F"], n))
    approved = np.zeros(n, dtype=bool)

    result = compute_attribute_fairness(defaults, approved, gender, "성별")

    assert result.status is FairnessStatus.COMPUTED
    assert result.proportional_parity_ratio is None
