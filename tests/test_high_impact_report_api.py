"""고영향 AI 사전진단 보고서 API 테스트."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api import high_impact_report as report_api
from app.main import app
from app.schemas.high_impact_report import (
    HighImpactReportResponse,
)
from app.services.storage import S3UploadError

client = TestClient(app)


def _payload() -> dict:
    return {
        "audit_id": 8,
        "assessment_id": 10,
        "audit_name": "신용평가 모델 감사",
        "model_name": "신용평가 모델",
        "model_version": "1.0",
        "assessed_at": "2026-07-31T10:00:00+09:00",
        "condition_met": True,
        "group_a_score": 0,
        "group_b_score": 0,
        "total_score": 0,
        "result": "HIGH_IMPACT",
        "answers": [
            {
                "question_code": "GATE_01",
                "question_text": "정성 게이트 문항 1",
                "stage": "QUALITATIVE",
                "group": "GATE",
                "answer": True,
                "weight": 0,
                "score": 0,
            },
            {
                "question_code": "GATE_02",
                "question_text": "정성 게이트 문항 2",
                "stage": "QUALITATIVE",
                "group": "GATE",
                "answer": False,
                "weight": 0,
                "score": 0,
            },
        ],
    }


def test_creates_high_impact_report(
    monkeypatch,
) -> None:
    def fake_generate(request):
        return HighImpactReportResponse(
            audit_id=request.audit_id,
            assessment_id=request.assessment_id,
            pdf_report_s3_key=(
                "high-impact-reports/8/10/run/report.pdf"
            ),
            word_report_s3_key=(
                "high-impact-reports/8/10/run/report.docx"
            ),
            generated_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(
        report_api,
        "generate_high_impact_report",
        fake_generate,
    )

    response = client.post(
        "/internal/v1/reports/high-impact-assessment",
        json=_payload(),
    )

    assert response.status_code == 200

    body = response.json()

    assert body["audit_id"] == 8
    assert body["assessment_id"] == 10
    assert body["pdf_report_s3_key"].endswith(
        "/report.pdf"
    )
    assert body["word_report_s3_key"].endswith(
        "/report.docx"
    )
    assert body["generated_at"]


def test_rejects_inconsistent_assessment_payload() -> None:
    payload = _payload()
    payload["result"] = "NOT_APPLICABLE"

    response = client.post(
        "/internal/v1/reports/high-impact-assessment",
        json=payload,
    )

    assert response.status_code == 422
    assert "최종 판정이 사전진단 기준과 일치하지 않습니다" in (
        response.text
    )


def test_returns_bad_gateway_when_s3_upload_fails(
    monkeypatch,
) -> None:
    def fake_generate(request):
        raise S3UploadError("S3 업로드 테스트 실패")

    monkeypatch.setattr(
        report_api,
        "generate_high_impact_report",
        fake_generate,
    )

    response = client.post(
        "/internal/v1/reports/high-impact-assessment",
        json=_payload(),
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "S3 업로드 테스트 실패"