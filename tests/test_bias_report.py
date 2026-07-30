"""편향진단 리포트 생성 서비스 테스트.

실제 LLM·S3 호출은 monkeypatch 하고, figure 생성(matplotlib)과 jinja2 렌더링은
실제로 수행해 HTML 조립을 검증한다.
"""

from pathlib import Path

import app.services.bias_report as bias_report_service
from app.schemas.audit import AuditRunResponse, FairnessMetricValues
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

    monkeypatch.setattr(
        bias_report_service, "analyze_fairness_s3", lambda fairness_request: _audit()
    )

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

    # 섹션별 5회 호출
    assert len(complete_calls) == 5

    html = upload_sink["html"]
    assert "편향진단 감사 리포트" in html
    assert "생성된 서술 문단" in html        # LLM 서술
    assert "CODE_GENDER" in html            # 집단표/지표표
    assert "Prop.Parity" in html            # 지표 표 헤더
    assert "data:image/png;base64," in html  # figure 임베드
