"""SHAP 지표 상태 판정 테스트."""

import pytest

from app.services.explainability.shap_pipeline import (
    overall_status,
    status_max,
    status_min,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.20, "PASS"),
        (0.20001, "WARNING"),
        (0.30, "WARNING"),
        (0.30001, "REVIEW"),
    ],
)
def test_status_max_supports_pass_warning_review(
    value,
    expected,
):
    """낮을수록 좋은 지표의 경계값을 정확히 판정한다."""

    assert status_max(
        value,
        pass_threshold=0.20,
        review_threshold=0.30,
    ) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.70, "PASS"),
        (0.69999, "WARNING"),
        (0.50, "WARNING"),
        (0.49999, "REVIEW"),
    ],
)
def test_status_min_supports_pass_warning_review(
    value,
    expected,
):
    """높을수록 좋은 지표의 경계값을 정확히 판정한다."""

    assert status_min(
        value,
        pass_threshold=0.70,
        review_threshold=0.50,
    ) == expected


@pytest.mark.parametrize(
    "invalid_value",
    [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_status_returns_not_evaluated_for_invalid_value(
    invalid_value,
):
    """측정할 수 없는 값은 상태 판정에서 제외한다."""

    assert status_min(
        invalid_value,
        pass_threshold=0.70,
        review_threshold=0.50,
    ) == "NOT_EVALUATED"
    assert status_max(
        invalid_value,
        pass_threshold=0.20,
        review_threshold=0.30,
    ) == "NOT_EVALUATED"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["PASS", "PASS", "PASS"], "PASS"),
        (["PASS", "WARNING", "PASS"], "WARNING"),
        (["PASS", "REVIEW", "WARNING"], "REVIEW"),
        (["PASS", "NOT_EVALUATED", "PASS"], "WARNING"),
        (["NOT_EVALUATED", "NOT_EVALUATED"], "NOT_EVALUATED"),
    ],
)
def test_overall_status_uses_highest_severity(
    statuses,
    expected,
):
    """종합 상태는 가장 심각한 핵심 지표 상태를 따른다."""

    assert overall_status(statuses) == expected


def test_status_without_review_threshold_keeps_warning():
    """보조 검증은 기존 PASS/WARNING 판정을 유지한다."""

    assert status_min(
        0.49,
        pass_threshold=0.50,
    ) == "WARNING"
    assert status_max(
        0.51,
        pass_threshold=0.50,
    ) == "WARNING"
