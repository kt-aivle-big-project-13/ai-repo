"""설명가능성 리포트 생성 서비스 테스트.

실제 LLM·S3 호출 없이 analyze/complete/upload 를 monkeypatch 하고,
jinja2 템플릿 렌더링은 실제로 수행해 HTML 조립을 검증한다.
"""

from pathlib import Path

import pytest

import app.services.report as report_service
from app.schemas.report import ReportRequest
from app.schemas.shap import (
    FeatureImportance,
    MetricDetail,
    SchemaValidation,
    ShapAnalysisResponse,
    ShapKeyMetrics,
    ShapMetricResult,
    ShapReport,
)


def _key_metrics() -> ShapKeyMetrics:
    metric = ShapMetricResult(
        metric="M", label="L", value=0.5, threshold=0.5,
        review_threshold=0.3, status="PASS",
    )
    return ShapKeyMetrics(
        sensitive_contribution_ratio=metric,
        global_explanation_stability=metric,
        explanation_fidelity=metric,
    )


def _report() -> ShapReport:
    return ShapReport(
        schema_validation=SchemaValidation(
            status="PASS", row_count=1000, model_feature_count=50,
            dataset_column_count=60, missing_model_features=[],
            missing_required_columns=[], duplicate_columns=[],
            audit_only_columns=["CODE_GENDER"],
        ),
        metrics=[
            MetricDetail(
                metric="GLOBAL_STABILITY", value=0.99, threshold=0.7,
                review_threshold=0.5, status="PASS",
                extra={"mean_top20_jaccard": 0.9},
            ),
            MetricDetail(
                metric="FIDELITY", value=0.46, threshold=0.5,
                review_threshold=0.3, status="WARNING",
                extra={"margin_reconstruction_r2": 0.8},
            ),
        ],
        global_importance_top=[
            FeatureImportance(
                rank=1, feature="EXT_SOURCE_3", mean_abs_shap=0.38,
                mean_signed_shap=-0.1, contribution_ratio=0.15,
                direction="RISK_DECREASE", is_sensitive=False, sensitive_group=None,
            ),
        ],
        sampling={
            "audit_sample_size": 5000.0, "dataset_row_count": 1000.0,
            "random_seed": 42.0,
        },
        thresholds={"explanation_fidelity_min": 0.5},
        manifest={
            "run_id": "shap_audit_X", "xgboost_version": "3.3.0",
            "model_file": "credit_model.json", "data_file": "audit_dataset.csv",
            "generated_at_utc": "2026-07-28T00:00:00Z",
        },
        limitations=["설명은 인과가 아니라 연관을 나타냄"],
    )


def _fake_response(with_report: bool = True) -> ShapAnalysisResponse:
    return ShapAnalysisResponse(
        pipeline_status="COMPLETED",
        overall_status="WARNING",
        key_metrics=_key_metrics(),
        report=_report() if with_report else None,
    )


def _request(**overrides) -> ReportRequest:
    values = {
        "audit_id": 42,
        "model_s3_key": "models/credit_model.json",
        "audit_dataset_s3_key": "datasets/audit_dataset.csv",
        "target_column": "TARGET",
        "sensitive_features": ["CODE_GENDER"],
    }
    values.update(overrides)
    return ReportRequest(**values)


def _patch_common(monkeypatch, upload_sink: dict, complete_calls: list):
    monkeypatch.setattr(
        report_service,
        "download_s3_object",
        lambda key, destination: Path(destination).write_bytes(b"x") or destination,
    )

    def fake_complete(prompt, system=None, **kwargs):
        complete_calls.append(prompt)
        return "생성된 서술 문단"

    monkeypatch.setattr(report_service, "complete", fake_complete)

    def fake_upload(source, key):
        upload_sink["key"] = key
        upload_sink["html"] = Path(source).read_text(encoding="utf-8")
        return key

    monkeypatch.setattr(report_service, "upload_s3_object", fake_upload)


def test_generate_report_renders_and_uploads(monkeypatch):
    upload_sink: dict = {}
    complete_calls: list = []
    _patch_common(monkeypatch, upload_sink, complete_calls)
    monkeypatch.setattr(
        report_service,
        "analyze_local_files",
        lambda request, model_path, dataset_path, output_dir: _fake_response(True),
    )

    result = report_service.generate_explainability_report(_request(audit_id=42))

    assert result.report_s3_key == "explainability-reports/42/shap_audit_X/report.html"
    assert result.format == "html"
    assert result.overall_status == "WARNING"
    assert result.generated_at

    # 섹션별 5회 호출
    assert len(complete_calls) == 5

    html = upload_sink["html"]
    assert "설명가능성 감사 리포트" in html
    assert "생성된 서술 문단" in html      # LLM 서술 삽입
    assert "EXT_SOURCE_3" in html          # 전역중요도 표
    assert "GLOBAL_STABILITY" in html      # 지표 표
    assert "WARNING" in html               # 상태 배지


def test_generate_report_raises_when_payload_missing(monkeypatch):
    upload_sink: dict = {}
    complete_calls: list = []
    _patch_common(monkeypatch, upload_sink, complete_calls)
    monkeypatch.setattr(
        report_service,
        "analyze_local_files",
        lambda request, model_path, dataset_path, output_dir: _fake_response(False),
    )

    with pytest.raises(report_service.ReportGenerationError):
        report_service.generate_explainability_report(_request())
