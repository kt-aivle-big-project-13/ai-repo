"""편향진단 리포트 생성 서비스 테스트.

실제 LLM·S3 호출은 monkeypatch 하고, figure 생성(matplotlib)과 jinja2 렌더링은
실제로 수행해 HTML 조립을 검증한다.
"""

from pathlib import Path

import app.services.bias_report as bias_report_service
from app.schemas.audit import AuditReportMeta, AuditRunResponse, FairnessMetricValues
from app.schemas.bias_report import BiasReportRequest
from app.schemas.fairness import AttributeFairness, FairnessStatus, GroupStat
from app.schemas.performance import PerformanceSummary
from app.schemas.scoring import CalibrationSource, ThresholdInfo, ThresholdMethod


def _attribute() -> AttributeFairness:
    return AttributeFairness(
        attribute="CODE_GENDER",
        status=FairnessStatus.COMPUTED,
        demographic_parity_difference=0.08,
        equal_opportunity_difference=0.05,
        equalized_odds_difference=0.06,
        proportional_parity_ratio=0.9,
        fpr_parity_difference=0.04,
        fdr_parity_difference=0.03,
        for_parity_difference=0.02,
        groups=[
            GroupStat(group="M", n=3000, approval_rate=0.88, actual_default_rate=0.09,
                      tp=2600, fp=250, tn=100, fn=50, auc=0.74),
            GroupStat(group="F", n=2500, approval_rate=0.80, actual_default_rate=0.07,
                      tp=1900, fp=150, tn=120, fn=330, auc=0.77),
        ],
        excluded_groups=[],
        note=None,
    )


def _audit() -> AuditRunResponse:
    attribute = _attribute()
    return AuditRunResponse(
        audit_id="42",
        audit_name="테스트 감사",
        threshold=ThresholdInfo(
            value=0.42, method=ThresholdMethod.TARGET_APPROVAL_RATE,
            basis="검증셋 90% 분위수", computed_from="validation_set",
        ),
        n_customers=5500,
        approval_rate=0.84,
        calibration_source=CalibrationSource.PLATFORM_COMPUTED,
        performance=PerformanceSummary(auc=0.755, accuracy=0.71),
        fairness_by_attribute={"CODE_GENDER": attribute},
        fairness_summary={
            "CODE_GENDER": FairnessMetricValues(
                DEMOGRAPHIC_PARITY=0.08, EQUAL_OPPORTUNITY=0.05, EQUALIZED_ODDS=0.06,
                PROPORTIONAL_PARITY=0.9, FPR_PARITY=0.04, FDR_PARITY=0.03, FOR_PARITY=0.02,
            )
        },
        warnings=[],
        report_meta=AuditReportMeta(
            model_s3_key="models/abc_credit_model.json",
            audit_dataset_s3_key="datasets/abc_audit_dataset.csv",
            model_sha256="a" * 64,
            audit_dataset_sha256="b" * 64,
            code_version="deadbeef",
            model_file="credit_model.json",
            n_features=50,
            n_categorical_features=8,
            data_n_rows=1000,
            data_n_columns=60,
            target_column="TARGET",
            protected_columns=["CODE_GENDER", "AGE_GROUP"],
            protected_in_model={"성별": False, "연령": True},
            schema_passed=True,
            schema_issues=[],
            run_id="bias_audit_20260730T000000Z",
            generated_at_utc="2026-07-30T00:00:00Z",
            xgboost_version="3.3.0",
            python_version="3.13.0",
            limitations=["공정성 지표는 인과가 아니라 격차를 나타냄"],
        ),
    )


def _request(**overrides) -> BiasReportRequest:
    values = {
        "audit_id": 42,
        "model_s3_key": "models/credit_model.json",
        "audit_dataset_s3_key": "datasets/audit_dataset.csv",
        "audit_name": "테스트 감사",
        "sensitive_features": ["CODE_GENDER"],
    }
    values.update(overrides)
    return BiasReportRequest(**values)


def test_generate_bias_report_renders_and_uploads(monkeypatch):
    complete_calls: list = []
    upload_sink: dict = {}

    analyze_calls: dict = {}

    def fake_analyze(fairness_request, include_report_meta=False):
        analyze_calls["include_report_meta"] = include_report_meta
        return _audit()

    monkeypatch.setattr(bias_report_service, "analyze_fairness_s3", fake_analyze)

    def fake_complete(prompt, system=None, **kwargs):
        complete_calls.append(prompt)
        return "생성된 서술 문단"

    monkeypatch.setattr(bias_report_service, "complete", fake_complete)

    def fake_upload(source, key):
        upload_sink["key"] = key
        upload_sink["html"] = Path(source).read_text(encoding="utf-8")
        return key

    monkeypatch.setattr(bias_report_service, "upload_s3_object", fake_upload)

    result = bias_report_service.generate_bias_report(_request(audit_id=42))

    assert result.format == "html"
    assert result.audit_id == 42
    assert result.report_s3_key.startswith("bias-reports/42/bias_")
    assert result.report_s3_key.endswith("/report.html")
    assert result.generated_at

    # 메타 요청 플래그가 분석 호출에 전달됐는지 검증
    assert analyze_calls["include_report_meta"] is True

    # 섹션별 5회 호출
    assert len(complete_calls) == 5

    html = upload_sink["html"]
    assert "편향진단 감사 리포트" in html
    assert "생성된 서술 문단" in html        # LLM 서술
    assert "CODE_GENDER" in html            # 집단표/지표표
    assert "Prop.Parity" in html            # 지표 표 헤더
    assert "data:image/png;base64," in html  # figure 임베드
    # report_meta 기반 보강 섹션 (모델정보·스키마검증·증적)
    assert "credit_model.json" in html      # 2장 모델 기본정보
    assert "입력 스키마 검증" in html        # 3장 스키마 검증
    assert "bias_audit_20260730T000000Z" in html  # 부록 증적·재현성
    assert "models/abc_credit_model.json" in html  # 입력 식별자(원본 S3 Key)
    assert "a" * 64 in html                         # 모델 콘텐츠 해시


def test_build_report_meta_maps_source_and_hashes(tmp_path):
    """원본 S3 Key·콘텐츠 해시·검증 정보가 리포트 메타로 옮겨진다."""

    from app.services.audit import _build_report_meta
    from app.schemas.audit import AuditInputSource
    from app.schemas.validation import (
        AuditDatasetInfo,
        IssueLevel,
        ModelSchema,
        ValidationIssue,
        ValidationResult,
    )

    model_path = tmp_path / "model.json"
    model_path.write_bytes(b"model-bytes")
    audit_path = tmp_path / "audit.csv"
    audit_path.write_bytes(b"audit-bytes")

    validation = ValidationResult(
        passed=True,
        issues=[ValidationIssue(level=IssueLevel.INFO, item="credit_model.json", message="로드 완료")],
        model_schema_info=ModelSchema(
            feature_names=["a", "b", "c"], categorical_features=["a"], n_features=3
        ),
        audit_dataset=AuditDatasetInfo(
            n_rows=1000, n_columns=60, target_column="TARGET",
            protected_columns=["CODE_GENDER", "AGE_GROUP"],
        ),
        protected_in_model={"성별": False, "연령": True},
    )
    source = AuditInputSource(
        model_s3_key="models/xyz_credit_model.json",
        audit_dataset_s3_key="datasets/xyz_audit_dataset.csv",
    )

    meta = _build_report_meta(validation, model_path, audit_path, None, source)

    import hashlib

    # 표시명은 임시 파일명이 아니라 원본 S3 Key basename 을 쓴다.
    assert meta.model_file == "xyz_credit_model.json"
    assert meta.model_s3_key == "models/xyz_credit_model.json"
    assert meta.audit_dataset_s3_key == "datasets/xyz_audit_dataset.csv"
    # 콘텐츠 해시는 실제 파일 바이트의 SHA-256 과 일치한다.
    assert meta.model_sha256 == hashlib.sha256(b"model-bytes").hexdigest()
    assert meta.audit_dataset_sha256 == hashlib.sha256(b"audit-bytes").hexdigest()
    assert meta.validation_dataset_sha256 is None
    assert meta.n_features == 3
    assert meta.data_n_rows == 1000
    assert meta.schema_passed is True
    assert meta.xgboost_version
    assert meta.limitations
