"""SHAP 설명가능성 분석 서비스."""

import tempfile
import numpy as np
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.schemas.shap import (
    ShapAnalysisRequest,
    ShapAnalysisResponse,
    ShapKeyMetrics,
    ShapMetricResult,
)
from app.services.shap_pipeline import (
    DEFAULT_CONFIG,
    run_shap_pipeline,
)
from app.services.storage import download_s3_object


def build_pipeline_config(
    target_column: str,
    sensitive_features: list[str],
) -> dict[str, Any]:
    """백엔드 요청값을 SHAP 파이프라인 설정으로 변환한다."""

    normalized_target = target_column.strip()
    normalized_sensitive_features = list(
        dict.fromkeys(
            feature.strip()
            for feature in sensitive_features
            if feature.strip()
        )
    )

    if not normalized_target:
        raise ValueError("target_column은 비어 있을 수 없습니다.")

    if not normalized_sensitive_features:
        raise ValueError("sensitive_features는 한 개 이상 필요합니다.")

    config = deepcopy(DEFAULT_CONFIG)
    config["target_column"] = normalized_target
    config["sensitive_groups"] = {
        feature.lower(): [feature]
        for feature in normalized_sensitive_features
    }

    deny_columns = set(config["disclosure_policy"]["deny_columns"])
    deny_columns.add(normalized_target)
    config["disclosure_policy"]["deny_columns"] = sorted(deny_columns)

    return config


def analyze_local_files(
    request: ShapAnalysisRequest,
    model_path: Path,
    dataset_path: Path,
    output_dir: Path,
) -> ShapAnalysisResponse:
    """로컬에 준비된 모델·데이터셋으로 SHAP 분석을 실행한다."""

    config = build_pipeline_config(
        target_column=request.target_column,
        sensitive_features=request.sensitive_features,
    )

    summary = run_shap_pipeline(
        model_path=model_path,
        data_path=dataset_path,
        output=output_dir,
        config=config,
        skip_lime=True,
    )

    key_metrics = summary["key_metrics"]

    return ShapAnalysisResponse(
        pipeline_status=summary["pipeline_status"],
        overall_status=summary["overall_status"],
        key_metrics=ShapKeyMetrics(
            sensitive_contribution_ratio=_to_metric_result(
                key_metrics["sensitive_contribution_ratio"],
                metric="SENSITIVE_CONTRIB",
                label="민감변수 기여비율",
            ),
            global_explanation_stability=_to_metric_result(
                key_metrics["global_explanation_stability"],
                metric="GLOBAL_STABILITY",
                label="전역 설명 안정성",
            ),
            explanation_fidelity=_to_metric_result(
                key_metrics["explanation_fidelity"],
                metric="FIDELITY",
                label="설명 충실성",
            ),
        ),
    )


def _to_metric_result(
    result: dict[str, Any],
    metric: str,
    label: str,
) -> ShapMetricResult:
    """파이프라인 KPI를 백엔드 저장 계약으로 변환한다."""

    raw_value = result.get("value")
    value = (
        float(raw_value)
        if raw_value is not None
        else None
    )

    if value is not None and not np.isfinite(value):
        value = None

    return ShapMetricResult(
        metric=metric,
        label=label,
        value=value,
        threshold=float(result["threshold"]),
        status=str(result["status"]),
    )


def analyze_s3_request(
    request: ShapAnalysisRequest,
) -> ShapAnalysisResponse:
    """S3 파일을 내려받아 SHAP 분석을 실행하고 임시 파일을 정리한다."""

    with tempfile.TemporaryDirectory(
        prefix=f"shap_audit_{request.audit_id}_"
    ) as temporary_directory:
        temporary_path = Path(temporary_directory)

        model_suffix = (
            Path(request.model_s3_key).suffix
            or ".json"
        )
        dataset_suffix = (
            Path(request.audit_dataset_s3_key).suffix
            or ".csv"
        )

        model_path = temporary_path / f"model{model_suffix}"
        dataset_path = temporary_path / f"audit_dataset{dataset_suffix}"
        output_dir = temporary_path / "outputs"

        download_s3_object(
            request.model_s3_key,
            model_path,
        )
        download_s3_object(
            request.audit_dataset_s3_key,
            dataset_path,
        )

        return analyze_local_files(
            request=request,
            model_path=model_path,
            dataset_path=dataset_path,
            output_dir=output_dir,
        )
