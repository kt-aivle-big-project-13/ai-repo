"""SHAP orchestration 서비스 테스트."""

from pathlib import Path

import pytest
import app.services.shap as shap_service
from app.schemas.shap import (
    ShapAnalysisRequest,
    ShapAnalysisResponse,
)


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
