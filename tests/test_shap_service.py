"""SHAP orchestration 서비스 테스트."""

import json
from pathlib import Path

import pytest
import app.services.shap as shap_service
from app.schemas.shap import (
    ShapAnalysisRequest,
    ShapAnalysisResponse,
    ShapKeyMetrics,
    ShapMetricResult,
)


def _key_metrics() -> ShapKeyMetrics:
    metric = ShapMetricResult(
        metric="M",
        label="L",
        value=0.5,
        threshold=0.5,
        review_threshold=0.3,
        status="PASS",
    )
    return ShapKeyMetrics(
        sensitive_contribution_ratio=metric,
        global_explanation_stability=metric,
        explanation_fidelity=metric,
    )


def _fake_summary(with_report: bool = True) -> dict:
    key_metrics = {
        "sensitive_contribution_ratio": {
            "value": 0.0, "threshold": 0.2, "review_threshold": 0.3, "status": "PASS",
        },
        "global_explanation_stability": {
            "value": 0.99, "threshold": 0.7, "review_threshold": 0.5, "status": "PASS",
        },
        "explanation_fidelity": {
            "value": 0.8, "threshold": 0.5, "review_threshold": 0.3, "status": "PASS",
        },
    }
    summary = {
        "pipeline_status": "COMPLETED",
        "overall_status": "PASS",
        "key_metrics": key_metrics,
    }
    if with_report:
        summary["report"] = {
            "schema_validation": {
                "status": "PASS", "row_count": 100, "model_feature_count": 5,
                "dataset_column_count": 8, "missing_model_features": [],
                "missing_required_columns": [], "duplicate_columns": [],
                "audit_only_columns": ["EXTRA"],
            },
            "metrics": [{
                "metric": "GLOBAL_STABILITY", "value": 0.99, "threshold": 0.7,
                "review_threshold": 0.5, "status": "PASS",
                "extra": {"mean_top20_jaccard": 0.9},
            }],
            "global_importance_top": [{
                "rank": 1, "feature": "AMT_CREDIT", "mean_abs_shap": 0.3,
                "mean_signed_shap": -0.1, "contribution_ratio": 0.25,
                "direction": "RISK_DECREASE", "is_sensitive": False,
                "sensitive_group": None,
            }],
            "sampling": {"audit_sample_size": 100.0},
            "thresholds": {"explanation_fidelity_min": 0.5},
            "manifest": {"run_id": "shap_audit_X", "xgboost_version": "3.3.0"},
            "limitations": ["설명은 인과가 아니라 연관을 나타냄"],
        }
    return summary


def _request(**overrides) -> ShapAnalysisRequest:
    values = {
        "audit_id": 1,
        "model_s3_key": "models/m.json",
        "audit_dataset_s3_key": "datasets/d.csv",
        "target_column": "TARGET",
        "sensitive_features": ["CODE_GENDER"],
    }
    values.update(overrides)
    return ShapAnalysisRequest(**values)


def test_analyze_s3_request_downloads_files_and_cleans_temp_directory(
    monkeypatch,
):
    """S3 파일을 내려받아 분석하고 임시 디렉터리를 정리한다."""

    request = ShapAnalysisRequest(
        audit_id=15,
        model_s3_key="models/credit_model.json",
        audit_dataset_s3_key="datasets/audit_dataset.csv",
        target_column="TARGET",
        sensitive_features=[
            "CODE_GENDER",
            "AGE_GROUP",
        ],
    )

    expected = ShapAnalysisResponse.model_validate(
        {
            "pipeline_status": "COMPLETED",
            "overall_status": "PASS",
            "key_metrics": {
                "sensitive_contribution_ratio": {
                    "metric": "SENSITIVE_CONTRIB",
                    "label": "민감변수 기여비율",
                    "value": 0.0,
                    "threshold": 0.2,
                    "review_threshold": 0.3,
                    "status": "PASS",
                },
                "global_explanation_stability": {
                    "metric": "GLOBAL_STABILITY",
                    "label": "전역 설명 안정성",
                    "value": 0.99,
                    "threshold": 0.7,
                    "review_threshold": 0.5,
                    "status": "PASS",
                },
                "explanation_fidelity": {
                    "metric": "FIDELITY",
                    "label": "설명 충실성",
                    "value": 0.8,
                    "threshold": 0.5,
                    "review_threshold": 0.3,
                    "status": "PASS",
                },
            },
        }
    )

    downloaded_keys: list[str] = []
    temporary_root: Path | None = None

    def fake_download(
        s3_key: str,
        destination: Path,
    ) -> Path:
        downloaded_keys.append(s3_key)
        destination.write_bytes(b"test")
        return destination

    def fake_analyze_local_files(
        request,
        model_path,
        dataset_path,
        output_dir,
    ):
        nonlocal temporary_root

        temporary_root = model_path.parent

        assert model_path.exists()
        assert dataset_path.exists()
        assert model_path.name == "model.json"
        assert dataset_path.name == "audit_dataset.csv"
        assert output_dir.parent == temporary_root

        return expected

    monkeypatch.setattr(
        shap_service,
        "download_s3_object",
        fake_download,
    )
    monkeypatch.setattr(
        shap_service,
        "analyze_local_files",
        fake_analyze_local_files,
    )

    result = shap_service.analyze_s3_request(request)

    assert result == expected
    assert downloaded_keys == [
        "models/credit_model.json",
        "datasets/audit_dataset.csv",
    ]
    assert temporary_root is not None
    assert not temporary_root.exists()

def test_build_pipeline_config_uses_request_values():
    """요청의 타깃·민감변수를 파이프라인 설정에 반영한다."""

    config = shap_service.build_pipeline_config(
        target_column=" TARGET ",
        sensitive_features=[
            " CODE_GENDER ",
            "AGE_GROUP",
            "CODE_GENDER",
        ],
    )

    assert config["target_column"] == "TARGET"
    assert config["sensitive_groups"] == {
        "code_gender": ["CODE_GENDER"],
        "age_group": ["AGE_GROUP"],
    }
    assert "TARGET" in config["disclosure_policy"]["deny_columns"]

@pytest.mark.parametrize(
    "invalid_value",
    [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_non_finite_metric_value_is_converted_to_none(
    invalid_value,
):
    metric = shap_service._to_metric_result(
        {
            "value": invalid_value,
            "threshold": 0.5,
            "review_threshold": 0.3,
            "status": "NOT_EVALUATED",
        },
        metric="FIDELITY",
        label="설명 충실성",
    )

    assert metric.value is None


def test_analyze_local_files_includes_report_when_requested(monkeypatch, tmp_path):
    """include_report=True 면 report 필드가 채워진다."""

    monkeypatch.setattr(
        shap_service, "run_shap_pipeline", lambda **kwargs: _fake_summary(True)
    )

    response = shap_service.analyze_local_files(
        request=_request(include_report=True),
        model_path=tmp_path / "m.json",
        dataset_path=tmp_path / "d.csv",
        output_dir=tmp_path / "out",
    )

    assert response.report is not None
    assert response.report.global_importance_top[0].feature == "AMT_CREDIT"
    assert response.report.schema_validation.audit_only_columns == ["EXTRA"]
    assert response.artifacts is None


def test_analyze_local_files_omits_report_by_default(monkeypatch, tmp_path):
    """기본(include_report=False)이면 확장 필드가 unset으로 빠진다."""

    monkeypatch.setattr(
        shap_service, "run_shap_pipeline", lambda **kwargs: _fake_summary(True)
    )

    response = shap_service.analyze_local_files(
        request=_request(),
        model_path=tmp_path / "m.json",
        dataset_path=tmp_path / "d.csv",
        output_dir=tmp_path / "out",
    )

    assert response.report is None
    dumped = response.model_dump(exclude_unset=True)
    assert "report" not in dumped
    assert "artifacts" not in dumped
    assert "artifacts_status" not in dumped


def test_persist_artifacts_uploads_and_sets_status(monkeypatch, tmp_path):
    """업로드 성공 시 artifacts와 UPLOADED 상태를 채운다."""

    output = tmp_path / "outputs"
    (output / "metadata").mkdir(parents=True)
    (output / "metadata" / "audit_manifest.json").write_text(
        json.dumps({"run_id": "shap_audit_X"}), encoding="utf-8"
    )

    monkeypatch.setattr(
        shap_service,
        "upload_directory",
        lambda directory, prefix: [
            {"name": "summary.json", "s3_key": f"{prefix}/summary.json", "kind": "json"}
        ],
    )
    monkeypatch.setattr(shap_service, "get_configured_bucket", lambda: "bkt")

    base = ShapAnalysisResponse(
        pipeline_status="COMPLETED",
        overall_status="PASS",
        key_metrics=_key_metrics(),
    )

    result = shap_service._persist_artifacts(
        _request(audit_id=7, include_report=True), base, output
    )

    assert result.artifacts_status == "UPLOADED"
    assert result.artifacts.bucket == "bkt"
    assert result.artifacts.prefix == "explainability/7/shap_audit_X"
    assert result.artifacts.files[0].s3_key == (
        "explainability/7/shap_audit_X/summary.json"
    )


def test_persist_artifacts_upload_failure_keeps_report(monkeypatch, tmp_path):
    """업로드 실패해도 report는 유지되고 상태만 UPLOAD_FAILED로 표시된다."""

    output = tmp_path / "outputs"
    (output / "metadata").mkdir(parents=True)
    (output / "metadata" / "audit_manifest.json").write_text(
        json.dumps({"run_id": "r"}), encoding="utf-8"
    )

    def boom(directory, prefix):
        raise shap_service.S3UploadError("업로드 실패")

    monkeypatch.setattr(shap_service, "upload_directory", boom)

    base = ShapAnalysisResponse(
        pipeline_status="COMPLETED",
        overall_status="PASS",
        key_metrics=_key_metrics(),
    )

    result = shap_service._persist_artifacts(
        _request(include_report=True), base, output
    )

    assert result.artifacts_status == "UPLOAD_FAILED"
    assert result.artifacts is None
    assert result.key_metrics == base.key_metrics
