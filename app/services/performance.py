"""모델 판별 성능 계산 (편향 보고서 성능 섹션).

공정성 지표가 '집단 간 격차'를 본다면, 여기서는 모델이 연체를 얼마나 잘
가려내는지(AUC)와 승인·거절 예측의 정확도를 본다. 집단별 AUC 는 공정성 계산
(`compute_attribute_fairness`)이 이 모듈의 `group_auc` 를 재사용해 함께 구한다.

AUC 는 위험점수의 순위만 쓰므로 원점수/보정확률 어느 쪽으로 계산해도 같다. 결정
임계값과도 무관하다 — 그래서 임계값 정책과 분리해 모델 자체의 판별력을 본다.
"""

import numpy as np
from sklearn.metrics import roc_auc_score

from app.schemas.performance import PerformanceSummary


def group_auc(labels: np.ndarray, risk_scores: np.ndarray) -> float | None:
    """라벨(1=연체)과 위험점수로 ROC AUC 를 구한다.

    표본이 없거나 한 클래스만 있으면(전원 정상 또는 전원 연체) AUC 가 정의되지
    않아 None 을 돌려준다.
    """
    labels = np.asarray(labels, dtype=int)
    if labels.size == 0 or len(np.unique(labels)) < 2:
        return None
    return round(float(roc_auc_score(labels, np.asarray(risk_scores, dtype=float))), 4)


def compute_performance(
    actual_defaults: np.ndarray,
    risk_scores: np.ndarray,
    approved: np.ndarray,
) -> PerformanceSummary:
    """감사셋 전체의 AUC 와 정확도를 계산한다.

    - AUC: 위험점수가 실제 연체를 얼마나 잘 순위매기는지 (임계값과 무관)
    - accuracy: 거절(승인 아님)을 연체 예측으로 보고 실제 라벨과 맞은 비율
    """
    labels = np.asarray(actual_defaults, dtype=int)
    approved_bool = np.asarray(approved, dtype=bool)

    auc = group_auc(labels, risk_scores)
    predicted_default = ~approved_bool
    accuracy = (
        round(float((predicted_default == (labels == 1)).mean()), 4)
        if labels.size
        else None
    )

    note = None
    if auc is None:
        note = "감사셋에 정상 또는 연체 고객만 있어 AUC 를 계산할 수 없음"

    return PerformanceSummary(auc=auc, accuracy=accuracy, note=note)
