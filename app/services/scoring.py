"""XGBoost 위험점수 및 승인 임계값 산출 (이슈 #5).

감사 데이터의 고객별 연체 위험확률을 계산하고, 설정된 방식으로 승인·거절
기준값을 정해 고객별 결과를 만든다. 모델 스키마는 검증 단계(#4)가 이미 읽어둔
`ModelSchema` 를 그대로 받아 쓰므로 같은 확인을 반복하지 않는다.

위험점수가 높을수록 연체 위험이 크다. 기준값 **미만이면 승인**, 기준값
**이상이면 거절**이며, 승인율은 승인 고객 수 / 전체 고객 수로 계산한다.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

from app.schemas.scoring import (
    CalibrationSource,
    ScoringResult,
    ScoringSummary,
    ThresholdConfig,
    ThresholdInfo,
    ThresholdMethod,
)
from app.schemas.validation import ModelSchema
from app.services.validation import TARGET_COLUMN


def load_booster(model_path: Path) -> xgb.Booster:
    """감사 대상 모델을 로드한다."""
    booster = xgb.Booster()
    booster.load_model(model_path)
    return booster


def _iteration_range(schema: ModelSchema) -> tuple[int, int]:
    """early stopping 으로 고른 트리까지만 쓰도록 예측 구간을 정한다.

    학습 때 best_iteration 이후 트리는 검증 성능이 나빠져 버려진 것이므로,
    예측에도 포함하면 학습 시점과 다른 모델을 감사하게 된다.
    best_iteration 이 없으면 (0, 0) 으로 전체 트리를 쓴다.
    """
    if schema.best_iteration is None:
        return (0, 0)
    return (0, schema.best_iteration + 1)


def build_category_dtypes(
    frames: list[pd.DataFrame], schema: ModelSchema
) -> dict[str, pd.CategoricalDtype]:
    """범주형 피처의 category dtype 를 프레임 전체에서 공통으로 만든다.

    XGBoost 는 범주형을 pandas category 코드로 다루므로, 프레임마다 따로
    `astype("category")` 하면 등장하는 값에 따라 코드가 어긋나 감사셋과
    검증셋의 점수가 서로 다른 기준을 갖게 된다. 두 프레임의 값을 합쳐 하나의
    dtype 을 만들어 그런 어긋남을 막는다.
    """
    dtypes: dict[str, pd.CategoricalDtype] = {}
    for column in schema.categorical_features:
        values = pd.concat(
            [frame[column] for frame in frames if column in frame.columns],
            ignore_index=True,
        )
        categories = pd.Index(values.dropna().unique()).sort_values()
        dtypes[column] = pd.CategoricalDtype(categories=categories)
    return dtypes


def prepare_features(
    frame: pd.DataFrame,
    schema: ModelSchema,
    category_dtypes: dict[str, pd.CategoricalDtype] | None = None,
) -> pd.DataFrame:
    """감사 데이터를 모델이 학습한 컬럼 순서·타입으로 정리한다.

    컬럼 순서가 학습 때와 다르면 예측값이 조용히 틀어지므로 반드시 맞춘다.
    """
    prepared = pd.DataFrame(index=frame.index)
    categorical = set(schema.categorical_features)

    for column in schema.feature_names:
        if column in categorical:
            dtype = (category_dtypes or {}).get(column, "category")
            prepared[column] = frame[column].astype(dtype)
        else:
            prepared[column] = pd.to_numeric(frame[column], errors="coerce")

    return prepared


def predict_risk_scores(
    booster: xgb.Booster, features: pd.DataFrame, schema: ModelSchema
) -> np.ndarray:
    """고객별 연체 위험확률을 계산한다."""
    matrix = xgb.DMatrix(features, enable_categorical=True)
    scores = booster.predict(matrix, iteration_range=_iteration_range(schema))
    return np.asarray(scores, dtype=float)


def compute_threshold(
    config: ThresholdConfig,
    audit_scores: np.ndarray,
    validation_scores: np.ndarray | None = None,
) -> ThresholdInfo:
    """승인·거절 기준값을 정한다.

    목표 승인율 방식은 위험점수의 분위수를 기준값으로 삼는다. 검증셋 점수가
    있으면 그쪽에서 산출하는데, 감사셋으로 기준값을 정하면 감사 대상 집단에
    승인율을 맞춰버려 그 집단의 위험 분포가 기준에 섞여 들어가기 때문이다.
    """
    if config.method is ThresholdMethod.MANUAL:
        return ThresholdInfo(
            value=float(config.manual_threshold),
            method=config.method,
            basis="사용자가 직접 지정한 기준값",
            computed_from="user_input",
        )

    rate = config.target_approval_rate
    if validation_scores is not None and len(validation_scores) > 0:
        source_scores, computed_from = validation_scores, "validation_set"
        basis = f"검증셋 위험점수의 {rate:.0%} 분위수"
    else:
        source_scores, computed_from = audit_scores, "audit_set"
        basis = f"감사셋 위험점수의 {rate:.0%} 분위수 (검증셋 없음)"

    return ThresholdInfo(
        value=float(np.quantile(source_scores, rate)),
        method=config.method,
        basis=basis,
        computed_from=computed_from,
    )


def apply_threshold(scores: np.ndarray, threshold: float) -> np.ndarray:
    """기준값으로 고객별 승인 여부를 만든다. True 면 승인이다."""
    return scores < threshold


def calibrate_probabilities(
    audit_scores: np.ndarray,
    validation_scores: np.ndarray | None,
    validation_labels: np.ndarray | None,
) -> tuple[np.ndarray, CalibrationSource]:
    """위험점수를 실제 연체율에 맞춘 보정확률로 변환한다.

    XGBoost 원점수는 순위는 맞아도 절대적인 확률값은 실제 연체율과 어긋날 수
    있다. 검증셋의 (원점수 → 실제 연체) 관계로 isotonic 회귀를 학습해 감사셋
    점수를 보정한다. 검증셋이 없으면 보정할 수 없으므로 원점수를 그대로 두고
    unavailable 로 표시한다 — 이 경우 보정확률을 연체율로 해석하면 안 된다.
    """
    if (
        validation_scores is None
        or validation_labels is None
        or len(validation_scores) == 0
    ):
        return audit_scores.copy(), CalibrationSource.UNAVAILABLE

    isotonic = IsotonicRegression(out_of_bounds="clip")
    isotonic.fit(validation_scores, validation_labels)
    calibrated = isotonic.predict(audit_scores)
    return np.asarray(calibrated, dtype=float), CalibrationSource.PLATFORM_COMPUTED


def score_audit_dataset(
    model_path: Path,
    schema: ModelSchema,
    audit_frame: pd.DataFrame,
    threshold_config: ThresholdConfig | None = None,
    validation_frame: pd.DataFrame | None = None,
) -> ScoringResult:
    """감사 데이터를 채점하고 승인·거절 결과를 만든다.

    `validation_frame` 은 목표 승인율 기준값을 산출할 때만 쓰이며, 없으면
    감사셋에서 기준값을 잡는다.
    """
    config = threshold_config or ThresholdConfig()
    booster = load_booster(model_path)

    frames = [audit_frame] if validation_frame is None else [audit_frame, validation_frame]
    category_dtypes = build_category_dtypes(frames, schema)

    audit_features = prepare_features(audit_frame, schema, category_dtypes)
    audit_scores = predict_risk_scores(booster, audit_features, schema)

    validation_scores = None
    validation_labels = None
    if validation_frame is not None:
        validation_features = prepare_features(validation_frame, schema, category_dtypes)
        validation_scores = predict_risk_scores(booster, validation_features, schema)
        validation_labels = validation_frame[TARGET_COLUMN].astype(int).to_numpy()

    threshold = compute_threshold(config, audit_scores, validation_scores)
    approved = apply_threshold(audit_scores, threshold.value)
    calibrated_probs, calibration_source = calibrate_probabilities(
        audit_scores, validation_scores, validation_labels
    )

    summary = ScoringSummary(
        n_customers=len(audit_scores),
        threshold=threshold,
        n_approved=int(approved.sum()),
        n_rejected=int((~approved).sum()),
        approval_rate=float(approved.mean()),
        mean_risk_score=float(audit_scores.mean()),
        calibration_source=calibration_source,
    )

    return ScoringResult(
        risk_scores=audit_scores,
        calibrated_probs=calibrated_probs,
        approved=approved,
        threshold=threshold,
        calibration_source=calibration_source,
        summary=summary,
    )


def load_audit_frames(
    audit_path: Path, valid_path: Path | None = None
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """감사 데이터와 (있으면) 검증 데이터를 읽는다.

    검증 단계를 통과한 파일을 전제로 하므로 여기서 형식을 다시 확인하지 않는다.
    """
    audit_frame = pd.read_csv(audit_path)
    validation_frame = pd.read_csv(valid_path) if valid_path is not None else None
    return audit_frame, validation_frame


def actual_defaults(audit_frame: pd.DataFrame) -> np.ndarray:
    """공정성 지표 계산에 쓸 실제 연체 여부를 꺼낸다."""
    return audit_frame[TARGET_COLUMN].astype(int).to_numpy()
