"""감사 실행 API 및 오케스트레이션 테스트."""

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.audit import ThresholdRequest
from app.schemas.scoring import ThresholdMethod
from app.services.audit import ValidationBlockedError, build_threshold_config, run_audit

FEATURES = ["AMT_CREDIT", "EXT_SOURCE_1", "NAME_INCOME_TYPE"]
N_ROWS = 1200


def _make_frame(n_rows=N_ROWS, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "AMT_CREDIT": rng.normal(500_000, 100_000, n_rows),
            "EXT_SOURCE_1": rng.uniform(0, 1, n_rows),
            "NAME_INCOME_TYPE": rng.choice(["Working", "Pensioner"], n_rows),
            "TARGET": rng.integers(0, 2, n_rows),
            "CODE_GENDER": rng.choice(["M", "F"], n_rows),
            "AGE_GROUP": rng.choice(["30대", "40대", "50대"], n_rows),
        }
    )


@pytest.fixture
def audit_files(tmp_path):
    """검증을 통과하는 모델·감사 데이터 파일 한 세트."""
    frame = _make_frame()
    features = frame[FEATURES].copy()
    features["NAME_INCOME_TYPE"] = features["NAME_INCOME_TYPE"].astype("category")
    model = xgb.XGBClassifier(n_estimators=5, max_depth=3, enable_categorical=True)
    model.fit(features, frame["TARGET"])

    model_path = tmp_path / "credit_model.json"
    model.save_model(model_path)
    audit_path = tmp_path / "audit_dataset.csv"
    frame.to_csv(audit_path, index=False)
    return model_path, audit_path


def test_build_threshold_config_precedence():
    """수동 임계값이 목표 승인율보다 우선한다."""
    manual = build_threshold_config(ThresholdRequest(target_approval_rate=0.8, threshold=0.5))
    assert manual.method is ThresholdMethod.MANUAL
    assert manual.manual_threshold == 0.5

    rate = build_threshold_config(ThresholdRequest(target_approval_rate=0.8))
    assert rate.method is ThresholdMethod.TARGET_APPROVAL_RATE
    assert rate.target_approval_rate == 0.8

    default = build_threshold_config(None)
    assert default.method is ThresholdMethod.TARGET_APPROVAL_RATE


def test_run_audit_end_to_end(audit_files):
    model_path, audit_path = audit_files

    result = run_audit(model_path, audit_path, audit_name="테스트 감사")

    assert result.audit_name == "테스트 감사"
    assert result.audit_id
    assert result.n_customers == N_ROWS
    assert set(result.fairness_by_attribute) == {"CODE_GENDER", "AGE_GROUP"}
    assert set(result.fairness_summary) == {"CODE_GENDER", "AGE_GROUP"}
    assert hasattr(result.fairness_summary["CODE_GENDER"], "DEMOGRAPHIC_PARITY")
    assert hasattr(result.fairness_summary["CODE_GENDER"], "FNR_PARITY")

    # 성능 지표(AUC·정확도)와 집단별 AUC 가 함께 채워진다.
    assert result.performance.accuracy is not None
    gender_groups = result.fairness_by_attribute["CODE_GENDER"].groups
    assert all(g.auc is not None for g in gender_groups)


def test_run_audit_respects_sensitive_features(audit_files):
    model_path, audit_path = audit_files

    result = run_audit(
        model_path, audit_path, audit_name="a", sensitive_features="CODE_GENDER"
    )

    assert set(result.fairness_by_attribute) == {"CODE_GENDER"}


def test_run_audit_blocks_on_bad_input(audit_files, tmp_path):
    """감사 데이터에 TARGET 이 없으면 검증 BLOCK 으로 중단한다."""
    model_path, _ = audit_files
    bad_path = tmp_path / "audit_dataset.csv"
    _make_frame().drop(columns=["TARGET"]).to_csv(bad_path, index=False)

    with pytest.raises(ValidationBlockedError) as exc:
        run_audit(model_path, bad_path, audit_name="a")

    assert exc.value.issues


def _upload_files(model_path, audit_path):
    return {
        "model_file": ("credit_model.json", model_path.read_bytes(), "application/json"),
        "audit_dataset_file": ("audit_dataset.csv", audit_path.read_bytes(), "text/csv"),
    }


def test_api_success(audit_files):
    model_path, audit_path = audit_files
    client = TestClient(app)

    response = client.post(
        "/api/fairness/audits",
        files=_upload_files(model_path, audit_path),
        data={"audit_name": "2026 3분기 정기감사", "target_approval_rate": 0.85},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["audit_name"] == "2026 3분기 정기감사"
    assert body["n_customers"] == N_ROWS
    assert body["threshold"]["method"] == "target_approval_rate"
    assert "CODE_GENDER" in body["fairness_by_attribute"]


def test_api_validation_block_returns_422(audit_files, tmp_path):
    model_path, _ = audit_files
    bad_path = tmp_path / "audit_dataset.csv"
    _make_frame().drop(columns=["TARGET"]).to_csv(bad_path, index=False)
    client = TestClient(app)

    response = client.post(
        "/api/fairness/audits",
        files=_upload_files(model_path, bad_path),
        data={"audit_name": "a"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["issues"]


def test_api_requires_audit_name(audit_files):
    model_path, audit_path = audit_files
    client = TestClient(app)

    response = client.post(
        "/api/fairness/audits",
        files=_upload_files(model_path, audit_path),
    )

    assert response.status_code == 422  # audit_name 폼 필드 누락


def test_api_manual_threshold(audit_files):
    model_path, audit_path = audit_files
    client = TestClient(app)

    response = client.post(
        "/api/fairness/audits",
        files=_upload_files(model_path, audit_path),
        data={"audit_name": "a", "threshold": 0.5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["threshold"]["method"] == "manual"
    assert body["threshold"]["value"] == 0.5
