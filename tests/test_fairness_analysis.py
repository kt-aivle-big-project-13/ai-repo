"""공정성 감사 S3 어댑터 테스트.

계산 로직(`run_audit`)은 `test_audit_api.py`·`test_fairness.py` 가 본다. 여기서는
어댑터가 맡은 일만 본다: S3 Key 로 무엇을 내려받고, 요청 필드를 감사 인자로 어떻게
옮기고, 임시 디렉터리를 정리하는지.
"""

from pathlib import Path

import pytest

import app.services.fairness.fairness_analysis as fairness_analysis
from app.schemas.audit import AuditRunResponse, FairnessMetricValues
from app.schemas.fairness.fairness import AttributeFairness, FairnessStatus, GroupStat
from app.schemas.fairness.fairness_internal import FairnessAnalyzeRequest
from app.schemas.performance import PerformanceSummary
from app.schemas.scoring import CalibrationSource, ThresholdInfo, ThresholdMethod


def _request(**overrides) -> FairnessAnalyzeRequest:
    values = {
        "audit_id": 15,
        "model_s3_key": "models/credit_model.json",
        "audit_dataset_s3_key": "datasets/audit_dataset.csv",
        "audit_name": "1차 정기감사",
        "sensitive_features": ["CODE_GENDER", "AGE_GROUP"],
    }
    values.update(overrides)
    return FairnessAnalyzeRequest(**values)


def _response() -> AuditRunResponse:
    attribute = AttributeFairness(
        attribute="CODE_GENDER",
        status=FairnessStatus.COMPUTED,
        demographic_parity_difference=0.08,
        groups=[
            GroupStat(group="M", n=3000, approval_rate=0.88, actual_default_rate=0.09,
                      tp=2600, fp=250, tn=100, fn=50, auc=0.74),
        ],
        excluded_groups=[],
        note=None,
    )

    return AuditRunResponse(
        audit_id="15",
        audit_name="1차 정기감사",
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


@pytest.fixture
def spy(monkeypatch):
    """다운로드와 감사 실행을 가로채고 호출 인자를 남긴다."""

    calls: dict = {"downloads": [], "audit": None, "temp_dirs": []}

    def fake_download(s3_key: str, destination: Path) -> None:
        calls["downloads"].append((s3_key, destination))
        # 실제 다운로드처럼 파일을 만들어 둔다 — 경로가 임시 디렉터리 안인지 확인하기 위함.
        destination.write_bytes(b"stub")

    def fake_run_audit(**kwargs):
        calls["audit"] = kwargs
        calls["temp_dirs"].append(kwargs["model_path"].parent)
        return _response()

    monkeypatch.setattr(fairness_analysis, "download_s3_object", fake_download)
    monkeypatch.setattr(fairness_analysis, "run_audit", fake_run_audit)

    return calls


def test_downloads_model_and_dataset_and_returns_audit_result(spy):
    result = fairness_analysis.analyze_s3_request(_request())

    assert [key for key, _ in spy["downloads"]] == [
        "models/credit_model.json",
        "datasets/audit_dataset.csv",
    ]
    assert result.audit_id == "15"


def test_cleans_up_temporary_directory(spy):
    """감사 입력은 임시 디렉터리에 두고 실행이 끝나면 지운다."""

    fairness_analysis.analyze_s3_request(_request())

    temporary_directory = spy["temp_dirs"][0]

    # 실행 중에는 파일이 실제로 있었어야 한다.
    assert [path for _, path in spy["downloads"]] == [
        temporary_directory / "model.json",
        temporary_directory / "audit_dataset.csv",
    ]
    assert not temporary_directory.exists()


def test_skips_validation_dataset_when_not_given(spy):
    fairness_analysis.analyze_s3_request(_request())

    assert len(spy["downloads"]) == 2
    assert spy["audit"]["valid_path"] is None


def test_downloads_validation_dataset_when_given(spy):
    fairness_analysis.analyze_s3_request(
        _request(validation_dataset_s3_key="datasets/valid_processed.csv")
    )

    assert [key for key, _ in spy["downloads"]][2] == "datasets/valid_processed.csv"
    assert spy["audit"]["valid_path"] is not None
    assert spy["audit"]["valid_path"].name == "valid_processed.csv"


def test_infers_suffix_from_s3_key(spy):
    """확장자로 파일 형식을 판단하는 코드가 뒤에 있어 임시 파일명에도 확장자를 붙인다."""

    fairness_analysis.analyze_s3_request(_request(
        model_s3_key="models/credit_model.ubj",
        audit_dataset_s3_key="datasets/audit.tsv",
    ))

    assert spy["audit"]["model_path"].name == "model.ubj"
    assert spy["audit"]["audit_path"].name == "audit_dataset.tsv"


def test_falls_back_to_default_suffix_without_extension(spy):
    """Key 에 확장자가 없으면 모델은 .json, 데이터는 .csv 로 둔다."""

    fairness_analysis.analyze_s3_request(_request(
        model_s3_key="models/credit_model",
        audit_dataset_s3_key="datasets/audit_dataset",
    ))

    assert spy["audit"]["model_path"].name == "model.json"
    assert spy["audit"]["audit_path"].name == "audit_dataset.csv"


def test_maps_manual_threshold_to_threshold_field(spy):
    """`manual_threshold` 는 `ThresholdRequest.threshold` 로, 목표 승인율은 그대로 옮긴다.

    어느 쪽을 실제로 쓸지 고르는 건 이 어댑터가 아니라 `build_threshold_config` 이고,
    그 우선순위는 `test_audit_api.py::test_build_threshold_config_precedence` 가 본다.
    여기서는 필드가 뒤바뀌거나 누락되지 않는지만 확인한다.
    """

    fairness_analysis.analyze_s3_request(_request(
        target_approval_rate=0.9,
        manual_threshold=0.35,
    ))

    threshold_request = spy["audit"]["threshold_request"]

    assert threshold_request.threshold == 0.35
    assert threshold_request.target_approval_rate == 0.9


def test_maps_target_approval_rate_without_manual_threshold(spy):
    fairness_analysis.analyze_s3_request(_request(target_approval_rate=0.85))

    threshold_request = spy["audit"]["threshold_request"]

    assert threshold_request.threshold is None
    assert threshold_request.target_approval_rate == 0.85


def test_joins_sensitive_features_with_comma(spy):
    """run_audit 은 콤마로 구분된 문자열을 받는다."""

    fairness_analysis.analyze_s3_request(
        _request(sensitive_features=["CODE_GENDER", "AGE_GROUP", "OCCUPATION_TYPE"])
    )

    assert spy["audit"]["sensitive_features"] == "CODE_GENDER,AGE_GROUP,OCCUPATION_TYPE"


def test_omits_report_meta_by_default(spy):
    """백엔드 연동 경로는 증적을 만들지 않는다 — 리포트 전용 플래그다."""

    fairness_analysis.analyze_s3_request(_request())

    assert spy["audit"]["include_report_meta"] is False
    assert spy["audit"]["report_source"] is None


def test_includes_report_source_when_requested(spy):
    """증적에는 임시 다운로드 경로가 아니라 원본 S3 Key 가 들어가야 한다."""

    fairness_analysis.analyze_s3_request(
        _request(validation_dataset_s3_key="datasets/valid_processed.csv"),
        include_report_meta=True,
    )

    source = spy["audit"]["report_source"]

    assert spy["audit"]["include_report_meta"] is True
    assert source.model_s3_key == "models/credit_model.json"
    assert source.audit_dataset_s3_key == "datasets/audit_dataset.csv"
    assert source.validation_dataset_s3_key == "datasets/valid_processed.csv"


def test_passes_audit_identity(spy):
    fairness_analysis.analyze_s3_request(_request(audit_id=77, audit_name="재감사"))

    assert spy["audit"]["audit_id"] == "77"
    assert spy["audit"]["audit_name"] == "재감사"


def test_does_not_persist_result_by_default(spy, monkeypatch):
    uploaded: list = []
    monkeypatch.setattr(
        fairness_analysis, "upload_s3_object", lambda src, key: uploaded.append(key)
    )

    fairness_analysis.analyze_s3_request(_request())

    assert uploaded == []


def test_persists_result_when_requested(spy, monkeypatch):
    """편향 리포트가 재사용할 수 있도록 감사 결과를 통째로 남긴다."""

    uploaded: dict = {}

    def fake_upload(source, key):
        uploaded[key] = Path(source).read_text(encoding="utf-8")
        return key

    monkeypatch.setattr(fairness_analysis, "upload_s3_object", fake_upload)

    fairness_analysis.analyze_s3_request(_request(), persist_result=True)

    assert len(uploaded) == 1
    key, body = next(iter(uploaded.items()))
    assert key.startswith(f"fairness/{_request().audit_id}/fairness_")
    assert key.endswith("/audit_result.json")

    # 리포트가 그대로 되살릴 수 있어야 한다.
    restored = AuditRunResponse.model_validate_json(body)
    assert restored.audit_name == _response().audit_name


def test_persist_failure_does_not_fail_the_analysis(spy, monkeypatch):
    """결과는 이미 응답으로 돌아가므로 저장 실패로 분석까지 실패시키지 않는다."""

    def fail_upload(source, key):
        raise fairness_analysis.S3UploadError("업로드 실패")

    monkeypatch.setattr(fairness_analysis, "upload_s3_object", fail_upload)

    result = fairness_analysis.analyze_s3_request(_request(), persist_result=True)

    assert result.audit_name == _response().audit_name
