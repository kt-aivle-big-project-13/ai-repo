"""위험점수·임계값 산출 서비스 테스트."""

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from pydantic import ValidationError

from app.schemas.scoring import CalibrationSource, ThresholdConfig, ThresholdMethod
from app.schemas.validation import ModelSchema
from app.services.scoring import (
    apply_threshold,
    build_category_dtypes,
    calibrate_probabilities,
    compute_threshold,
    prepare_features,
    score_audit_dataset,
)

FEATURES = ["AMT_CREDIT", "EXT_SOURCE_1", "NAME_INCOME_TYPE"]
CATEGORICAL = ["NAME_INCOME_TYPE"]
N_ROWS = 500


@pytest.fixture
def schema():
    return ModelSchema(
        feature_names=FEATURES,
        categorical_features=CATEGORICAL,
        n_features=len(FEATURES),
        best_iteration=None,
    )


def _make_frame(n_rows: int = N_ROWS, seed: int = 0, income_types=("Working", "Pensioner")):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "AMT_CREDIT": rng.normal(500_000, 100_000, n_rows),
            "EXT_SOURCE_1": rng.uniform(0, 1, n_rows),
            "NAME_INCOME_TYPE": rng.choice(income_types, n_rows),
            "TARGET": rng.integers(0, 2, n_rows),
        }
    )


@pytest.fixture
def model_path(tmp_path):
    frame = _make_frame()
    features = frame[FEATURES].copy()
    features["NAME_INCOME_TYPE"] = features["NAME_INCOME_TYPE"].astype("category")

    model = xgb.XGBClassifier(n_estimators=5, max_depth=3, enable_categorical=True)
    model.fit(features, frame["TARGET"])

    path = tmp_path / "credit_model.json"
    model.save_model(path)
    return path


def test_prepare_features_reorders_columns(schema):
    """컬럼 순서가 뒤섞여 있어도 모델 학습 순서로 정렬한다."""
    frame = _make_frame()[["NAME_INCOME_TYPE", "TARGET", "EXT_SOURCE_1", "AMT_CREDIT"]]

    prepared = prepare_features(frame, schema)

    assert list(prepared.columns) == FEATURES


def test_prepare_features_casts_types(schema):
    frame = _make_frame()
    frame["AMT_CREDIT"] = frame["AMT_CREDIT"].astype(str)

    prepared = prepare_features(frame, schema)

    assert isinstance(prepared["NAME_INCOME_TYPE"].dtype, pd.CategoricalDtype)
    assert np.issubdtype(prepared["AMT_CREDIT"].dtype, np.number)


def test_shared_category_dtype_aligns_codes(schema):
    """한쪽에만 있는 범주값 때문에 코드가 어긋나지 않아야 한다."""
    audit = _make_frame(seed=0, income_types=("Working", "Pensioner", "Student"))
    validation = _make_frame(seed=1, income_types=("Working", "Pensioner"))

    dtypes = build_category_dtypes([audit, validation], schema)
    audit_prepared = prepare_features(audit, schema, dtypes)
    validation_prepared = prepare_features(validation, schema, dtypes)

    audit_categories = audit_prepared["NAME_INCOME_TYPE"].cat.categories
    validation_categories = validation_prepared["NAME_INCOME_TYPE"].cat.categories
    assert list(audit_categories) == list(validation_categories)
    assert "Student" in validation_categories


def test_threshold_from_target_approval_rate():
    scores = np.linspace(0, 1, 101)
    config = ThresholdConfig(target_approval_rate=0.90)

    info = compute_threshold(config, scores)

    assert info.value == pytest.approx(0.90, abs=0.01)
    assert info.computed_from == "audit_set"


def test_threshold_prefers_validation_scores():
    """검증셋이 있으면 그쪽 분위수로 기준값을 잡는다."""
    audit_scores = np.linspace(0.0, 0.5, 100)
    validation_scores = np.linspace(0.5, 1.0, 100)
    config = ThresholdConfig(target_approval_rate=0.50)

    info = compute_threshold(config, audit_scores, validation_scores)

    assert info.value == pytest.approx(0.75, abs=0.02)
    assert info.computed_from == "validation_set"


def test_manual_threshold_is_used_as_is():
    config = ThresholdConfig(method=ThresholdMethod.MANUAL, manual_threshold=0.42)

    info = compute_threshold(config, np.linspace(0, 1, 100))

    assert info.value == 0.42
    assert info.computed_from == "user_input"


def test_manual_method_requires_threshold_value():
    with pytest.raises(ValidationError):
        ThresholdConfig(method=ThresholdMethod.MANUAL)


def test_apply_threshold_boundary():
    """기준값과 정확히 같으면 거절이다."""
    scores = np.array([0.1, 0.5, 0.9])

    approved = apply_threshold(scores, 0.5)

    assert approved.tolist() == [True, False, False]


def test_score_audit_dataset_approval_rate(model_path, schema):
    """목표 승인율대로 승인 비율이 맞춰진다."""
    audit_frame = _make_frame(seed=2)
    config = ThresholdConfig(target_approval_rate=0.80)

    result = score_audit_dataset(model_path, schema, audit_frame, config)

    assert result.summary.n_customers == N_ROWS
    assert result.summary.approval_rate == pytest.approx(0.80, abs=0.03)
    assert result.summary.n_approved + result.summary.n_rejected == N_ROWS


def test_score_audit_dataset_with_validation_frame(model_path, schema):
    audit_frame = _make_frame(seed=2)
    validation_frame = _make_frame(seed=3)

    result = score_audit_dataset(
        model_path, schema, audit_frame, validation_frame=validation_frame
    )

    assert result.threshold.computed_from == "validation_set"
    assert len(result.risk_scores) == N_ROWS


def test_scores_are_probabilities(model_path, schema):
    result = score_audit_dataset(model_path, schema, _make_frame(seed=4))

    assert result.risk_scores.min() >= 0.0
    assert result.risk_scores.max() <= 1.0


def test_column_order_does_not_change_scores(model_path, schema):
    """컬럼 순서가 달라도 같은 점수가 나와야 한다."""
    audit_frame = _make_frame(seed=5)
    shuffled = audit_frame[["TARGET", "NAME_INCOME_TYPE", "AMT_CREDIT", "EXT_SOURCE_1"]]

    baseline = score_audit_dataset(model_path, schema, audit_frame)
    reordered = score_audit_dataset(model_path, schema, shuffled)

    np.testing.assert_allclose(baseline.risk_scores, reordered.risk_scores)


def test_calibration_unavailable_without_validation(model_path, schema):
    """검증셋이 없으면 보정확률은 원점수 그대로이고 unavailable 로 표시한다."""
    result = score_audit_dataset(model_path, schema, _make_frame(seed=7))

    assert result.calibration_source is CalibrationSource.UNAVAILABLE
    np.testing.assert_array_equal(result.risk_scores, result.calibrated_probs)


def test_calibration_uses_validation_frame(model_path, schema):
    result = score_audit_dataset(
        model_path, schema, _make_frame(seed=7), validation_frame=_make_frame(seed=8)
    )

    assert result.calibration_source is CalibrationSource.PLATFORM_COMPUTED
    assert result.summary.calibration_source is CalibrationSource.PLATFORM_COMPUTED
    assert result.calibrated_probs.min() >= 0.0
    assert result.calibrated_probs.max() <= 1.0


def test_calibrate_probabilities_is_monotonic():
    """isotonic 보정은 원점수 순서를 뒤집지 않는다."""
    validation_scores = np.linspace(0, 1, 200)
    validation_labels = (validation_scores > 0.5).astype(int)
    audit_scores = np.array([0.1, 0.3, 0.6, 0.9])

    calibrated, source = calibrate_probabilities(
        audit_scores, validation_scores, validation_labels
    )

    assert source is CalibrationSource.PLATFORM_COMPUTED
    assert np.all(np.diff(calibrated) >= 0)


def test_calibration_recovers_default_rate(model_path, schema):
    """보정확률의 평균이 실제 연체율에 가까워야 한다."""
    validation = _make_frame(seed=9, n_rows=2000)
    audit = _make_frame(seed=10, n_rows=2000)

    result = score_audit_dataset(model_path, schema, audit, validation_frame=validation)

    assert result.calibrated_probs.mean() == pytest.approx(
        audit["TARGET"].mean(), abs=0.05
    )


def test_best_iteration_limits_trees(model_path):
    """best_iteration 이 있으면 그 지점까지만 써서 다른 점수가 나온다."""
    audit_frame = _make_frame(seed=6)
    full = ModelSchema(
        feature_names=FEATURES,
        categorical_features=CATEGORICAL,
        n_features=len(FEATURES),
        best_iteration=None,
    )
    truncated = full.model_copy(update={"best_iteration": 0})

    full_result = score_audit_dataset(model_path, full, audit_frame)
    truncated_result = score_audit_dataset(model_path, truncated, audit_frame)

    assert not np.allclose(full_result.risk_scores, truncated_result.risk_scores)
