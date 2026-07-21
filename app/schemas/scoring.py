"""위험점수·임계값 산출 스키마."""

from dataclasses import dataclass
from enum import Enum

import numpy as np
from pydantic import BaseModel, Field, model_validator


class ThresholdMethod(str, Enum):
    """승인·거절 기준값을 정하는 방식."""

    TARGET_APPROVAL_RATE = "target_approval_rate"
    MANUAL = "manual"


class ThresholdConfig(BaseModel):
    """임계값 산출 설정.

    `TARGET_APPROVAL_RATE` 면 목표 승인율에 맞는 기준값을 데이터에서 산출하고,
    `MANUAL` 이면 사용자가 준 값을 그대로 쓴다.
    """

    method: ThresholdMethod = ThresholdMethod.TARGET_APPROVAL_RATE
    target_approval_rate: float | None = Field(
        default=0.90, ge=0.0, le=1.0, description="위험점수가 낮은 쪽부터 승인할 비율"
    )
    manual_threshold: float | None = Field(
        default=None, description="사용자가 직접 지정한 기준값. 이 값 이상이면 거절"
    )

    @model_validator(mode="after")
    def _check_required_field(self) -> "ThresholdConfig":
        if self.method is ThresholdMethod.MANUAL and self.manual_threshold is None:
            raise ValueError("manual 방식은 manual_threshold 가 필요합니다")
        if self.method is ThresholdMethod.TARGET_APPROVAL_RATE and self.target_approval_rate is None:
            raise ValueError("target_approval_rate 방식은 target_approval_rate 가 필요합니다")
        return self


class ThresholdInfo(BaseModel):
    """결정된 임계값과 그 근거."""

    value: float
    method: ThresholdMethod
    basis: str = Field(description="임계값을 어떻게 정했는지에 대한 설명")
    computed_from: str = Field(
        description="분위수를 계산한 데이터 (validation_set / audit_set / user_input)"
    )


class CalibrationSource(str, Enum):
    """보정확률을 어떻게 얻었는지."""

    PLATFORM_COMPUTED = "platform_computed"
    UNAVAILABLE = "unavailable"


class ScoringSummary(BaseModel):
    """감사 데이터 전체의 채점 결과 요약. API 응답에 그대로 실린다."""

    n_customers: int
    threshold: ThresholdInfo
    n_approved: int
    n_rejected: int
    approval_rate: float = Field(description="승인 고객 수 / 전체 고객 수")
    mean_risk_score: float
    calibration_source: CalibrationSource = Field(
        description="보정확률 산출 방식. unavailable 이면 위험점수를 그대로 참고값으로 둔 것"
    )


@dataclass
class ScoringResult:
    """채점 결과 전체.

    고객별 배열은 6만 건 규모라 API 응답에 싣지 않고, 공정성 지표 계산 단계가
    메모리에서 그대로 받아 쓴다. 외부로 나가는 요약은 `summary` 를 쓴다.

    `risk_scores` 는 승인·거절 결정에 쓰는 원점수, `calibrated_probs` 는
    실제 연체율에 맞춘 보정확률이다. 결정은 원점수로만 하고, 보정확률은
    위험확률 해석·캘리브레이션 용도로만 쓴다.
    """

    risk_scores: np.ndarray
    calibrated_probs: np.ndarray
    approved: np.ndarray
    threshold: ThresholdInfo
    calibration_source: CalibrationSource
    summary: ScoringSummary
