"""감사 입력 검증 서비스 테스트.

실제 감사 파일(`credit_model.json` 20MB 급)은 저장소에 두지 않으므로, 같은 구조를
갖는 소형 모델·데이터를 만들어 검증 규칙을 확인한다.
"""

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb

from app.schemas.validation import IssueLevel
from app.services.validation import validate_audit_inputs

FEATURES = ["AMT_CREDIT", "EXT_SOURCE_1", "NAME_INCOME_TYPE"]
CATEGORICAL = ["NAME_INCOME_TYPE"]
N_ROWS = 400


def _make_frame(n_rows: int = N_ROWS, seed: int = 0) -> pd.DataFrame:
    """모델 피처 + 정답값 + 보호속성을 갖춘 감사 데이터."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "AMT_CREDIT": rng.normal(500_000, 100_000, n_rows),
            "EXT_SOURCE_1": rng.uniform(0, 1, n_rows),
            "NAME_INCOME_TYPE": pd.Categorical(
                rng.choice(["Working", "Pensioner"], n_rows)
            ),
            "TARGET": rng.integers(0, 2, n_rows),
            "CODE_GENDER": rng.choice(["M", "F"], n_rows),
            "AGE_GROUP": rng.choice(["30대", "40대"], n_rows),
        }
    )


@pytest.fixture
def model_path(tmp_path):
    """FEATURES 를 입력으로 갖는 소형 XGBoost 모델 파일."""
    frame = _make_frame()
    model = xgb.XGBClassifier(n_estimators=3, max_depth=2, enable_categorical=True)
    model.fit(frame[FEATURES], frame["TARGET"])

    path = tmp_path / "credit_model.json"
    model.save_model(path)
    return path


@pytest.fixture
def audit_path(tmp_path):
    path = tmp_path / "audit_dataset.csv"
    _make_frame().to_csv(path, index=False)
    return path


def _items(result, level: IssueLevel) -> set[str]:
    return {issue.item for issue in result.issues if issue.level is level}


def test_valid_inputs_pass(model_path, audit_path):
    result = validate_audit_inputs(model_path, audit_path)

    assert result.passed
    assert not _items(result, IssueLevel.BLOCK)
    assert result.model_schema_info.feature_names == FEATURES
    assert result.model_schema_info.categorical_features == CATEGORICAL
    assert result.audit_dataset.n_rows == N_ROWS


def test_protected_attributes_excluded_from_model(model_path, audit_path):
    """보호속성이 모델 입력에 없으면 두 항목 모두 False 로 보고한다."""
    result = validate_audit_inputs(model_path, audit_path)

    assert result.protected_in_model == {"성별": False, "연령": False}


def test_protected_attribute_used_by_model_warns(tmp_path):
    """모델이 CODE_GENDER 를 직접 학습했으면 WARN 이지만 감사는 진행한다."""
    frame = _make_frame()
    frame["CODE_GENDER"] = pd.Categorical(frame["CODE_GENDER"])
    features = [*FEATURES, "CODE_GENDER"]

    model = xgb.XGBClassifier(n_estimators=3, max_depth=2, enable_categorical=True)
    model.fit(frame[features], frame["TARGET"])
    model_path = tmp_path / "credit_model.json"
    model.save_model(model_path)

    audit_path = tmp_path / "audit_dataset.csv"
    frame.to_csv(audit_path, index=False)

    result = validate_audit_inputs(model_path, audit_path)

    assert result.passed
    assert result.protected_in_model["성별"] is True
    assert "성별" in _items(result, IssueLevel.WARN)


def test_missing_model_file(tmp_path, audit_path):
    result = validate_audit_inputs(tmp_path / "없는모델.json", audit_path)

    assert not result.passed
    assert result.model_schema_info is None


def test_unloadable_model_file(tmp_path, audit_path):
    """XGBoost 모델이 아닌 JSON 은 BLOCK 으로 잡고 즉시 중단한다."""
    model_path = tmp_path / "credit_model.json"
    model_path.write_text('{"not": "a model"}', encoding="utf-8")

    result = validate_audit_inputs(model_path, audit_path)

    assert not result.passed
    assert model_path.name in _items(result, IssueLevel.BLOCK)


def test_empty_audit_dataset(model_path, tmp_path):
    audit_path = tmp_path / "audit_dataset.csv"
    _make_frame().iloc[:0].to_csv(audit_path, index=False)

    result = validate_audit_inputs(model_path, audit_path)

    assert not result.passed
    assert audit_path.name in _items(result, IssueLevel.BLOCK)


def test_missing_model_feature_in_audit_data(model_path, tmp_path):
    audit_path = tmp_path / "audit_dataset.csv"
    _make_frame().drop(columns=["EXT_SOURCE_1"]).to_csv(audit_path, index=False)

    result = validate_audit_inputs(model_path, audit_path)

    assert not result.passed
    assert audit_path.name in _items(result, IssueLevel.BLOCK)


@pytest.mark.parametrize("column", ["TARGET", "CODE_GENDER", "AGE_GROUP"])
def test_required_columns_missing(model_path, tmp_path, column):
    audit_path = tmp_path / "audit_dataset.csv"
    _make_frame().drop(columns=[column]).to_csv(audit_path, index=False)

    result = validate_audit_inputs(model_path, audit_path)

    assert not result.passed
    assert column in _items(result, IssueLevel.BLOCK)


def test_target_with_non_binary_values(model_path, tmp_path):
    frame = _make_frame()
    frame.loc[0, "TARGET"] = 7
    audit_path = tmp_path / "audit_dataset.csv"
    frame.to_csv(audit_path, index=False)

    result = validate_audit_inputs(model_path, audit_path)

    assert not result.passed
    assert "TARGET" in _items(result, IssueLevel.BLOCK)


def test_single_group_protected_column(model_path, tmp_path):
    """성별이 한 집단뿐이면 집단 비교가 불가능하므로 BLOCK."""
    frame = _make_frame()
    frame["CODE_GENDER"] = "F"
    audit_path = tmp_path / "audit_dataset.csv"
    frame.to_csv(audit_path, index=False)

    result = validate_audit_inputs(model_path, audit_path)

    assert not result.passed
    assert "CODE_GENDER" in _items(result, IssueLevel.BLOCK)


def test_small_group_warns_but_passes(model_path, tmp_path):
    """표본이 적은 집단은 경고만 남기고 감사는 진행한다."""
    frame = _make_frame()
    frame.loc[frame.index[:5], "AGE_GROUP"] = "60대이상"
    audit_path = tmp_path / "audit_dataset.csv"
    frame.to_csv(audit_path, index=False)

    result = validate_audit_inputs(model_path, audit_path)

    assert result.passed
    assert "AGE_GROUP" in _items(result, IssueLevel.WARN)


def test_optional_valid_dataset_accepted(model_path, audit_path, tmp_path):
    valid_path = tmp_path / "valid_processed.csv"
    _make_frame(seed=1)[[*FEATURES, "TARGET"]].to_csv(valid_path, index=False)

    result = validate_audit_inputs(model_path, audit_path, valid_path)

    assert result.passed
    assert result.valid_dataset_usable


def test_optional_valid_dataset_absent(model_path, audit_path, tmp_path):
    """선택 파일이 없어도 감사는 통과하되 보정에는 쓸 수 없다."""
    result = validate_audit_inputs(model_path, audit_path, tmp_path / "없는파일.csv")

    assert result.passed
    assert not result.valid_dataset_usable


def test_broken_valid_dataset_does_not_block(model_path, audit_path, tmp_path):
    """선택 파일이 망가져도 감사 자체는 막지 않는다."""
    valid_path = tmp_path / "valid_processed.csv"
    _make_frame(seed=1).drop(columns=["EXT_SOURCE_1"]).to_csv(valid_path, index=False)

    result = validate_audit_inputs(model_path, audit_path, valid_path)

    assert result.passed
    assert not result.valid_dataset_usable
    assert valid_path.name in _items(result, IssueLevel.WARN)
