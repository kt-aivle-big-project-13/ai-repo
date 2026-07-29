"""모델 판별 성능(AUC·정확도) 계산 테스트."""

import numpy as np
import pytest

from app.services.performance import compute_performance, group_auc


def test_group_auc_matches_perfect_separation():
    """위험점수가 라벨을 완벽히 분리하면 AUC 는 1.0 이다."""
    labels = np.array([0, 0, 1, 1])
    scores = np.array([0.1, 0.2, 0.8, 0.9])

    assert group_auc(labels, scores) == pytest.approx(1.0)


def test_group_auc_none_when_single_class():
    """한 클래스만 있으면 AUC 가 정의되지 않아 None 이다."""
    assert group_auc(np.array([0, 0, 0]), np.array([0.1, 0.5, 0.9])) is None
    assert group_auc(np.array([], dtype=int), np.array([])) is None


def test_accuracy_counts_reject_as_predicted_default():
    """거절(승인 아님)을 연체 예측으로 보고 실제 라벨과 맞춘 비율을 센다.

    4명 중: 정상·승인(맞음), 연체·거절(맞음), 정상·거절(틀림), 연체·승인(틀림)
    → 정확도 0.5.
    """
    defaults = np.array([0, 1, 0, 1])
    approved = np.array([True, False, False, True])
    scores = np.array([0.1, 0.9, 0.2, 0.8])

    result = compute_performance(defaults, scores, approved)

    assert result.accuracy == pytest.approx(0.5)
    assert result.auc == pytest.approx(1.0)
    assert result.note is None


def test_note_when_auc_undefined():
    """감사셋에 한 클래스만 있으면 AUC 는 None 이고 사유가 기록된다."""
    defaults = np.zeros(10, dtype=int)
    approved = np.array([True] * 6 + [False] * 4)
    scores = np.random.default_rng(0).random(10)

    result = compute_performance(defaults, scores, approved)

    assert result.auc is None
    assert result.note is not None
    assert result.accuracy is not None
