"""설명가능성 리포트 생성 서비스 테스트.

실제 LLM·S3 호출 없이 analyze/complete/upload 를 monkeypatch 하고,
jinja2 템플릿 렌더링은 실제로 수행해 HTML 조립을 검증한다.
"""

import json
from pathlib import Path

import pytest

import app.services.report.report as report_service
from app.schemas.report.report import ReportRequest
from app.schemas.explainability.shap import (
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

    # 재사용할 산출물이 없는 상태를 기본으로 둔다. 실제 S3를 조회하면 로컬 .env 유무에
    # 따라 결과가 달라지므로 테스트마다 명시적으로 정한다.
    monkeypatch.setattr(report_service, "find_latest_prefix", lambda base: None)

    def fake_complete(prompt, system=None, **kwargs):
        complete_calls.append(prompt)
        return "생성된 서술 문단"

    monkeypatch.setattr(report_service, "complete", fake_complete)

    def fake_render_html_to_pdf(html_path, pdf_path):
        Path(pdf_path).write_bytes(b"%PDF-test")
        return Path(pdf_path).resolve()

    monkeypatch.setattr(
        report_service,
        "render_html_to_pdf",
        fake_render_html_to_pdf,
    )

    def fake_render_report_to_docx(
        *,
        meta,
        report,
        narratives,
        figures,
        docx_path,
    ):
        Path(docx_path).write_bytes(b"PK-docx-test")
        return Path(docx_path).resolve()

    monkeypatch.setattr(
        report_service,
        "render_report_to_docx",
        fake_render_report_to_docx,
    )

    def fake_upload(source, key):
        upload_sink[key] = Path(source).read_bytes()
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
    assert (
        result.pdf_report_s3_key
        == "explainability-reports/42/shap_audit_X/report.pdf"
    )
    assert (
        result.word_report_s3_key
        == "explainability-reports/42/shap_audit_X/report.docx"
    )
    assert result.format == "html"
    assert result.overall_status == "WARNING"
    assert result.generated_at

    # 섹션별 5회 호출
    assert len(complete_calls) == 5

    html = upload_sink[result.report_s3_key].decode("utf-8")
    assert upload_sink[result.pdf_report_s3_key].startswith(b"%PDF-")
    assert upload_sink[result.word_report_s3_key].startswith(b"PK")
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


def test_generate_report_raises_when_docx_generation_fails(
    monkeypatch,
):
    upload_sink: dict = {}
    complete_calls: list = []
    _patch_common(monkeypatch, upload_sink, complete_calls)

    monkeypatch.setattr(
        report_service,
        "analyze_local_files",
        lambda request, model_path, dataset_path, output_dir: (
            _fake_response(True)
        ),
    )

    def fail_to_render_docx(**kwargs):
        raise report_service.DocxGenerationError(
            "Word 변환 실패"
        )

    monkeypatch.setattr(
        report_service,
        "render_report_to_docx",
        fail_to_render_docx,
    )

    with pytest.raises(
        report_service.ReportGenerationError,
        match="Word 리포트 생성에 실패",
    ):
        report_service.generate_explainability_report(
            _request(audit_id=42)
        )

    assert upload_sink == {}


def _persisted_payload(top_n: int = 3) -> dict:
    """SHAP 파이프라인이 S3 에 남기는 report_payload.json 형태."""

    payload = _report().model_dump()
    payload["global_importance_top"] = [
        {
            "rank": rank,
            "feature": f"FEATURE_{rank}",
            "mean_abs_shap": 0.5 / rank,
            "mean_signed_shap": -0.1,
            "contribution_ratio": 0.1,
            "direction": "RISK_DECREASE",
            "is_sensitive": False,
            "sensitive_group": None,
        }
        for rank in range(1, top_n + 1)
    ]
    return payload


def _patch_prior_artifacts(monkeypatch, prefix: str, payload: dict) -> list[str]:
    """S3 에 남아 있는 이전 분석 산출물을 흉내 낸다. 요청된 Key 목록을 돌려준다."""

    requested: list[str] = []
    monkeypatch.setattr(report_service, "find_latest_prefix", lambda base: prefix)

    def fake_download(key, destination):
        requested.append(key)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if key.endswith("report_payload.json"):
            destination.write_text(json.dumps(payload), encoding="utf-8")
        elif key.endswith("explainability_summary.json"):
            destination.write_text(
                json.dumps({"overall_status": "PASS"}), encoding="utf-8"
            )
        else:
            destination.write_bytes(b"png-bytes")
        return destination

    monkeypatch.setattr(report_service, "download_s3_object", fake_download)
    return requested


def test_generate_report_reuses_prior_analysis_without_rerunning(monkeypatch):
    upload_sink: dict = {}
    complete_calls: list = []
    _patch_common(monkeypatch, upload_sink, complete_calls)

    requested = _patch_prior_artifacts(
        monkeypatch, "explainability/42/shap_audit_X", _persisted_payload()
    )

    def fail_if_called(**kwargs):
        raise AssertionError("산출물을 재사용할 수 있으면 분석을 다시 돌리면 안 된다")

    monkeypatch.setattr(report_service, "analyze_local_files", fail_if_called)

    result = report_service.generate_explainability_report(_request(audit_id=42))

    assert result.overall_status == "PASS"
    assert result.report_s3_key == "explainability-reports/42/shap_audit_X/report.html"

    # 모델·데이터셋은 내려받지 않는다. 산출물만 읽는다.
    assert not any("models/" in key or "datasets/" in key for key in requested)
    assert "explainability/42/shap_audit_X/summary/report_payload.json" in requested

    html = upload_sink[result.report_s3_key].decode("utf-8")
    assert "FEATURE_1" in html


def test_generate_report_truncates_persisted_importance_to_requested_top_n(
    monkeypatch,
):
    upload_sink: dict = {}
    complete_calls: list = []
    _patch_common(monkeypatch, upload_sink, complete_calls)
    _patch_prior_artifacts(
        monkeypatch, "explainability/42/shap_audit_X", _persisted_payload(top_n=10)
    )
    monkeypatch.setattr(
        report_service,
        "analyze_local_files",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("재실행 금지")),
    )

    captured: dict = {}

    def capture_render(context):
        captured.update(context)
        return "<html></html>"

    monkeypatch.setattr(report_service, "_render_html", capture_render)

    report_service.generate_explainability_report(
        _request(audit_id=42, report_top_n=4)
    )

    importance = captured["report"].global_importance_top
    assert [item.feature for item in importance] == [
        "FEATURE_1",
        "FEATURE_2",
        "FEATURE_3",
        "FEATURE_4",
    ]
    assert captured["report"].sampling["report_top_n"] == 4.0


def test_generate_report_falls_back_when_artifacts_unavailable(monkeypatch):
    upload_sink: dict = {}
    complete_calls: list = []
    _patch_common(monkeypatch, upload_sink, complete_calls)

    monkeypatch.setattr(
        report_service,
        "find_latest_prefix",
        lambda base: "explainability/42/shap_audit_X",
    )

    def fake_download(key, destination):
        if key.startswith("explainability/42/"):
            raise report_service.S3DownloadError("산출물 없음")
        return Path(destination).write_bytes(b"x") or destination

    monkeypatch.setattr(report_service, "download_s3_object", fake_download)

    analyzed: list = []

    def fake_analyze(request, model_path, dataset_path, output_dir):
        analyzed.append(request.audit_id)
        return _fake_response(True)

    monkeypatch.setattr(report_service, "analyze_local_files", fake_analyze)

    result = report_service.generate_explainability_report(_request(audit_id=42))

    assert analyzed == [42]
    assert result.overall_status == "WARNING"
