"""SHAP 설명가능성 분석 서비스."""

import json
import logging
import tempfile
import numpy as np
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.schemas.explainability.shap import (
    ArtifactFile,
    FeatureImportance,
    MetricDetail,
    SchemaValidation,
    ShapAnalysisRequest,
    ShapAnalysisResponse,
    ShapArtifacts,
    ShapKeyMetrics,
    ShapMetricResult,
    ShapReport,
)
from app.services.explainability.shap_pipeline import (
    DEFAULT_CONFIG,
    run_shap_pipeline,
)
from app.services.storage import (
    S3ConfigurationError,
    S3UploadError,
    download_s3_object,
    get_configured_bucket,
    upload_directory,
)

logger = logging.getLogger(__name__)


def build_pipeline_config(
    target_column: str,
    sensitive_features: list[str],
    report_top_n: int = 20,
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
    config["report_top_n"] = int(report_top_n)
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
        report_top_n=request.report_top_n,
    )

    summary = run_shap_pipeline(
        model_path=model_path,
        data_path=dataset_path,
        output=output_dir,
        config=config,
        skip_lime=True,
    )

    key_metrics = summary["key_metrics"]

    fields: dict[str, Any] = {
        "pipeline_status": summary["pipeline_status"],
        "overall_status": summary["overall_status"],
        "key_metrics": ShapKeyMetrics(
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
    }

    # include_report 일 때만 확장 필드를 채운다 (기존 계약 유지).
    if request.include_report and "report" in summary:
        fields["report"] = _to_report(summary["report"])

    return ShapAnalysisResponse(**fields)


def _to_report(payload: dict[str, Any]) -> ShapReport:
    """파이프라인 report payload 를 응답 스키마로 변환한다."""

    return ShapReport(
        schema_validation=SchemaValidation(**payload["schema_validation"]),
        metrics=[MetricDetail(**metric) for metric in payload["metrics"]],
        global_importance_top=[
            FeatureImportance(**feature)
            for feature in payload["global_importance_top"]
        ],
        sampling=payload["sampling"],
        thresholds=payload["thresholds"],
        manifest=payload["manifest"],
        limitations=payload["limitations"],
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
        review_threshold=float(result["review_threshold"]),
        status=str(result["status"]),
    )


def _persist_artifacts(
    request: ShapAnalysisRequest,
    response: ShapAnalysisResponse,
    output_dir: Path,
) -> ShapAnalysisResponse:
    """산출물을 S3에 업로드하고 artifacts·상태를 채운 응답을 돌려준다.

    업로드가 실패해도 report 는 그대로 두고 상태만 UPLOAD_FAILED 로 표시한다
    (파이프라인은 결정적이라 동일 입력 재호출로 재생성할 수 있다).
    """

    try:
        manifest_path = output_dir / "metadata" / "audit_manifest.json"
        run_id = json.loads(manifest_path.read_text(encoding="utf-8"))["run_id"]
        prefix = f"explainability/{request.audit_id}/{run_id}"
        files = upload_directory(output_dir, prefix)
        artifacts = ShapArtifacts(
            bucket=get_configured_bucket(),
            prefix=prefix,
            files=[ArtifactFile(**file) for file in files],
        )
        return response.model_copy(
            update={"artifacts": artifacts, "artifacts_status": "UPLOADED"}
        )
    except (
        S3UploadError,
        S3ConfigurationError,
        OSError,
        KeyError,
        ValueError,
    ) as exception:
        logger.warning(
            "SHAP 증적 업로드 실패: audit_id=%s (%s)",
            request.audit_id,
            exception,
        )
        return response.model_copy(update={"artifacts_status": "UPLOAD_FAILED"})


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

        response = analyze_local_files(
            request=request,
            model_path=model_path,
            dataset_path=dataset_path,
            output_dir=output_dir,
        )

        if not request.include_report:
            return response

        return _persist_artifacts(request, response, output_dir)
