"""SHAP FastAPI 엔드포인트 테스트."""

from fastapi.testclient import TestClient

import app.api.shap as shap_api
from app.main import app
from app.schemas.shap import (
    ShapAnalysisResponse,
    ShapKeyMetrics,
    ShapMetricResult,
)

client = TestClient(app)


def test_analyze_shap_returns_backend_contract(monkeypatch):
    """SHAP API가 백엔드 저장 DTO와 같은 응답을 반환한다."""

    def fake_analyze(request):
        assert request.audit_id == 1
        assert request.model_s3_key == "models/model.json"
        assert request.audit_dataset_s3_key == "datasets/audit.csv"
        assert request.target_column == "TARGET"
        assert request.sensitive_features == [
            "CODE_GENDER",
            "AGE_GROUP",
        ]

        return ShapAnalysisResponse(
            pipeline_status="COMPLETED",
            overall_status="WARNING",
            key_metrics=ShapKeyMetrics(
                sensitive_contribution_ratio=ShapMetricResult(
                    metric="SENSITIVE_CONTRIB",
                    label="민감변수 기여비율",
                    value=0.0647,
                    threshold=0.2,
                    review_threshold=0.3,
                    status="PASS",
                ),
                global_explanation_stability=ShapMetricResult(
                    metric="GLOBAL_STABILITY",
                    label="전역 설명 안정성",
                    value=0.9996,
                    threshold=0.7,
                    review_threshold=0.5,
                    status="PASS",
                ),
                explanation_fidelity=ShapMetricResult(
                    metric="FIDELITY",
                    label="설명 충실성",
                    value=0.4843,
                    threshold=0.5,
                    review_threshold=0.3,
                    status="WARNING",
                ),
            ),
        )

    monkeypatch.setattr(
        shap_api,
        "analyze_s3_request",
        fake_analyze,
    )

    response = client.post(
        "/internal/v1/shap/analyze",
        json={
            "audit_id": 1,
            "model_s3_key": "models/model.json",
            "audit_dataset_s3_key": "datasets/audit.csv",
            "target_column": "TARGET",
            "sensitive_features": [
                "CODE_GENDER",
                "AGE_GROUP",
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "pipeline_status": "COMPLETED",
        "overall_status": "WARNING",
        "key_metrics": {
            "sensitive_contribution_ratio": {
                "metric": "SENSITIVE_CONTRIB",
                "label": "민감변수 기여비율",
                "value": 0.0647,
                "threshold": 0.2,
                "review_threshold": 0.3,
                "status": "PASS",
            },
            "global_explanation_stability": {
                "metric": "GLOBAL_STABILITY",
                "label": "전역 설명 안정성",
                "value": 0.9996,
                "threshold": 0.7,
                "review_threshold": 0.5,
                "status": "PASS",
            },
            "explanation_fidelity": {
                "metric": "FIDELITY",
                "label": "설명 충실성",
                "value": 0.4843,
                "threshold": 0.5,
                "review_threshold": 0.3,
                "status": "WARNING",
            },
        },
    }

def test_analyze_shap_rejects_empty_sensitive_features():
    response = client.post(
        "/internal/v1/shap/analyze",
        json={
            "audit_id": 1,
            "model_s3_key": "models/model.json",
            "audit_dataset_s3_key": "datasets/audit.csv",
            "target_column": "TARGET",
            "sensitive_features": [],
        },
    )

    assert response.status_code == 422


def test_analyze_shap_returns_503_when_s3_is_not_configured(
    monkeypatch,
):
    from app.services.storage import S3ConfigurationError

    def fail_analysis(request):
        raise S3ConfigurationError(
            "AWS_S3_BUCKET 환경변수가 설정되지 않았습니다."
        )

    monkeypatch.setattr(
        shap_api,
        "analyze_s3_request",
        fail_analysis,
    )

    response = client.post(
        "/internal/v1/shap/analyze",
        json={
            "audit_id": 1,
            "model_s3_key": "models/model.json",
            "audit_dataset_s3_key": "datasets/audit.csv",
            "target_column": "TARGET",
            "sensitive_features": ["CODE_GENDER"],
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "AWS_S3_BUCKET 환경변수가 설정되지 않았습니다."
        )
    }
