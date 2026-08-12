"""내부 연동 API 라우터 테스트.

라우터가 하는 일은 사실상 전부 **예외 → HTTP 상태 매핑**이다. 서비스 로직은 각
서비스 테스트가 보므로, 여기서는 서비스를 통째로 가로채고 라우터만 본다.

이 매핑은 백엔드와의 계약이다. 예를 들어 S3 다운로드 실패가 502 가 아니라 500 으로
나가면 백엔드는 재시도 대상으로 보지 않는다. 그래서 상태코드를 하나하나 고정한다.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api.bias.bias_report as bias_api
import app.api.chat.chat as chat_api
import app.api.compliance.compliance_report as compliance_api
import app.api.fairness.fairness_internal as fairness_api
import app.api.improvement.improvement_guide as improvement_api
import app.api.report as report_api
from app.main import app
from app.schemas.audit import AuditRunResponse, FairnessMetricValues
from app.schemas.bias.bias_report import BiasReportResponse
from app.schemas.chat.chat import ChatAnswerResponse
from app.schemas.compliance.compliance_report import ComplianceReportResponse
from app.schemas.fairness.fairness import AttributeFairness, FairnessStatus, GroupStat
from app.schemas.improvement.improvement_guide import ImprovementGuideResponse
from app.schemas.performance import PerformanceSummary
from app.schemas.report.report import ReportResponse
from app.schemas.scoring import CalibrationSource, ThresholdInfo, ThresholdMethod
from app.schemas.validation import IssueLevel, ValidationIssue
from app.services.audit import ValidationBlockedError
from app.services.docx_common import DocxGenerationError
from app.services.llm import LLMConfigurationError, LLMRequestError
from app.services.report.report import ReportGenerationError
from app.services.report.report_pdf import PdfGenerationError
from app.services.storage import S3ConfigurationError, S3DownloadError, S3UploadError

client = TestClient(app)


@dataclass(frozen=True)
class Endpoint:
    """라우터 하나를 테스트하는 데 필요한 것들."""

    name: str
    path: str
    module: Any
    service: str  # 라우터 모듈이 부르는 서비스 함수 이름
    payload: dict
    response: Any
    # 이 라우터가 매핑하는 (예외, 기대 상태코드). 라우터마다 잡는 예외가 다르다.
    error_cases: list[tuple[Exception, int]] = field(default_factory=list)


def _audit_response() -> AuditRunResponse:
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
        fairness_by_attribute={
            "CODE_GENDER": AttributeFairness(
                attribute="CODE_GENDER",
                status=FairnessStatus.COMPUTED,
                demographic_parity_difference=0.08,
                groups=[GroupStat(group="M", n=3000, approval_rate=0.88,
                                  actual_default_rate=0.09, tp=2600, fp=250,
                                  tn=100, fn=50, auc=0.74)],
                excluded_groups=[],
                note=None,
            )
        },
        fairness_summary={
            "CODE_GENDER": FairnessMetricValues(
                DEMOGRAPHIC_PARITY=0.08, EQUAL_OPPORTUNITY=0.05, EQUALIZED_ODDS=0.06,
                PROPORTIONAL_PARITY=0.9, FPR_PARITY=0.04, FDR_PARITY=0.03, FOR_PARITY=0.02,
            )
        },
        warnings=[],
    )


BLOCKED = ValidationBlockedError([
    ValidationIssue(
        level=IssueLevel.BLOCK,
        item="audit_dataset.csv",
        message="실제값 컬럼(TARGET)이 없음",
    )
])

# 문서 렌더 실패는 요청을 고쳐도 해결되지 않아 500 으로 간다 (502·422 가 아니다).
RENDER_FAILURES = [
    (PdfGenerationError("PDF 렌더 실패"), 500),
    (DocxGenerationError("Word 렌더 실패"), 500),
]

COMMON_FAILURES = [
    (S3ConfigurationError("AWS_S3_BUCKET 환경변수가 설정되지 않았습니다."), 503),
    (LLMConfigurationError("LLM API 키가 없습니다."), 503),
    (LLMRequestError("LLM 호출 실패"), 502),
    (S3UploadError("업로드 실패"), 502),
    (ValueError("입력이 올바르지 않습니다."), 422),
]

BIAS = Endpoint(
    name="bias",
    path="/internal/v1/reports/bias",
    module=bias_api,
    service="generate_bias_report",
    payload={
        "audit_id": 42,
        "model_s3_key": "models/credit_model.json",
        "audit_dataset_s3_key": "datasets/audit_dataset.csv",
        "audit_name": "테스트 감사",
        "sensitive_features": ["CODE_GENDER"],
    },
    response=BiasReportResponse(
        audit_id=42,
        report_s3_key="bias-reports/42/run/report.html",
        pdf_report_s3_key="bias-reports/42/run/report.pdf",
        word_report_s3_key="bias-reports/42/run/report.docx",
        format="html",
        generated_at="2026-08-06T00:00:00Z",
    ),
    error_cases=[
        *COMMON_FAILURES,
        *RENDER_FAILURES,
        (S3DownloadError("다운로드 실패"), 502),
        (BLOCKED, 422),
    ],
)

FAIRNESS = Endpoint(
    name="fairness",
    path="/internal/v1/fairness/analyze",
    module=fairness_api,
    service="analyze_s3_request",
    payload={
        "audit_id": 15,
        "model_s3_key": "models/credit_model.json",
        "audit_dataset_s3_key": "datasets/audit_dataset.csv",
        "audit_name": "1차 정기감사",
        "sensitive_features": ["CODE_GENDER", "AGE_GROUP"],
    },
    response=_audit_response(),
    error_cases=[
        (S3ConfigurationError("AWS_S3_BUCKET 환경변수가 설정되지 않았습니다."), 503),
        (S3DownloadError("다운로드 실패"), 502),
        (ValueError("입력이 올바르지 않습니다."), 422),
        (BLOCKED, 422),
    ],
)

COMPLIANCE = Endpoint(
    name="compliance",
    path="/internal/v1/reports/compliance",
    module=compliance_api,
    service="generate_compliance_report",
    payload={
        "audit_id": 42,
        "audit_name": "테스트 감사",
        "self_check_answers": [
            {"item_code": "NOTICE", "label": "AI 심사 사실 사전 고지 여부", "answer": True},
        ],
        "regulation_mappings": [
            {
                "law_name": "AI 기본법",
                "article_no": "제27조",
                "content": "조항 본문",
                "compliance": "COMPLIANT",
                "evidence": "자율점검 기반 자동 매칭",
            }
        ],
    },
    response=ComplianceReportResponse(
        audit_id=42,
        report_s3_key="compliance-reports/42/run/report.html",
        pdf_report_s3_key="compliance-reports/42/run/report.pdf",
        word_report_s3_key="compliance-reports/42/run/report.docx",
        format="html",
        compliant_count=1,
        non_compliant_count=0,
        pending_count=0,
        generated_at="2026-08-06T00:00:00Z",
    ),
    error_cases=[*COMMON_FAILURES, *RENDER_FAILURES],
)

IMPROVEMENT = Endpoint(
    name="improvement",
    path="/internal/v1/reports/improvement",
    module=improvement_api,
    service="generate_improvement_guide",
    payload={"audit_id": 42, "audit_name": "테스트 감사"},
    response=ImprovementGuideResponse(
        audit_id=42,
        report_s3_key="improvement-guides/42/run/report.html",
        pdf_report_s3_key="improvement-guides/42/run/report.pdf",
        word_report_s3_key="improvement-guides/42/run/report.docx",
        format="html",
        high_priority_count=1,
        medium_priority_count=0,
        low_priority_count=0,
        generated_at="2026-08-06T00:00:00Z",
    ),
    error_cases=[*COMMON_FAILURES, *RENDER_FAILURES],
)

EXPLAINABILITY = Endpoint(
    name="explainability",
    path="/internal/v1/reports/explainability",
    module=report_api,
    service="generate_explainability_report",
    payload={
        "audit_id": 42,
        "model_s3_key": "models/credit_model.json",
        "audit_dataset_s3_key": "datasets/audit_dataset.csv",
        "target_column": "TARGET",
        "sensitive_features": ["CODE_GENDER"],
    },
    response=ReportResponse(
        audit_id=42,
        report_s3_key="reports/42/run/report.html",
        pdf_report_s3_key="reports/42/run/report.pdf",
        word_report_s3_key="reports/42/run/report.docx",
        format="html",
        overall_status="PASS",
        generated_at="2026-08-06T00:00:00Z",
    ),
    error_cases=[
        *COMMON_FAILURES,
        (S3DownloadError("다운로드 실패"), 502),
        (ReportGenerationError("리포트 입력이 부족합니다."), 422),
    ],
)

CHAT = Endpoint(
    name="chat",
    path="/internal/v1/chat/answers",
    module=chat_api,
    service="generate_chat_answer",
    payload={"audit_id": 42, "question": "공정성 지표가 기준을 넘었나요?"},
    response=ChatAnswerResponse(
        audit_id=42,
        answer="근거가 부족해 답변할 수 없습니다.",
        citations=[],
        grounding_status="NOT_GROUNDED",
        generated_at="2026-08-06T00:00:00Z",
    ),
    error_cases=[
        (LLMConfigurationError("LLM API 키가 없습니다."), 503),
        (LLMRequestError("LLM 호출 실패"), 502),
        (ValueError("질문이 올바르지 않습니다."), 422),
    ],
)

ENDPOINTS = [BIAS, FAIRNESS, COMPLIANCE, IMPROVEMENT, EXPLAINABILITY, CHAT]

ERROR_CASES = [
    pytest.param(endpoint, exception, expected,
                 id=f"{endpoint.name}-{type(exception).__name__}-{expected}")
    for endpoint in ENDPOINTS
    for exception, expected in endpoint.error_cases
]


def _patch(monkeypatch, endpoint: Endpoint, handler) -> None:
    monkeypatch.setattr(endpoint.module, endpoint.service, handler)


def _patch_raising(monkeypatch, endpoint: Endpoint, exception: Exception) -> None:
    # 라우터마다 서비스 호출 시 넘기는 키워드 인자가 달라(예: 공정성 분석은
    # include_report_meta·persist_result) 스텁은 전부 받아 넘긴다.
    def fail(request, **kwargs):
        raise exception

    _patch(monkeypatch, endpoint, fail)


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
def test_returns_service_response_as_is(monkeypatch, endpoint: Endpoint):
    """백엔드가 그대로 저장하는 응답이라 라우터가 필드를 바꾸면 안 된다."""

    _patch(monkeypatch, endpoint, lambda request, **kwargs: endpoint.response)

    response = client.post(endpoint.path, json=endpoint.payload)

    assert response.status_code == 200
    assert response.json() == endpoint.response.model_dump(mode="json")


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
def test_passes_request_body_to_service(monkeypatch, endpoint: Endpoint):
    """라우터는 파싱한 요청을 그대로 넘긴다."""

    received = {}

    def capture(request, **kwargs):
        received["audit_id"] = request.audit_id
        return endpoint.response

    _patch(monkeypatch, endpoint, capture)

    client.post(endpoint.path, json=endpoint.payload)

    assert received["audit_id"] == endpoint.payload["audit_id"]


@pytest.mark.parametrize("endpoint,exception,expected", ERROR_CASES)
def test_maps_exception_to_status(
    monkeypatch, endpoint: Endpoint, exception: Exception, expected: int
):
    """설정 누락 503 · 외부 호출 실패 502 · 입력 문제 422 · 렌더 실패 500."""

    _patch_raising(monkeypatch, endpoint, exception)

    response = client.post(endpoint.path, json=endpoint.payload)

    assert response.status_code == expected


@pytest.mark.parametrize("endpoint", [BIAS, FAIRNESS], ids=lambda e: e.name)
def test_validation_block_response_carries_issues(monkeypatch, endpoint: Endpoint):
    """어느 파일의 무엇이 막았는지 백엔드가 사용자에게 그대로 보여준다."""

    _patch_raising(monkeypatch, endpoint, BLOCKED)

    detail = client.post(endpoint.path, json=endpoint.payload).json()["detail"]

    assert detail["detail"] == "감사 입력 검증에 실패했습니다"
    assert [issue["message"] for issue in detail["issues"]] == [
        "실제값 컬럼(TARGET)이 없음"
    ]
    assert detail["issues"][0]["item"] == "audit_dataset.csv"


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
def test_hides_internal_message_on_unexpected_error(monkeypatch, endpoint: Endpoint):
    """예상 못한 오류의 내부 메시지는 밖으로 내보내지 않는다."""

    _patch_raising(monkeypatch, endpoint, RuntimeError("psycopg2 connection string leaked"))

    response = client.post(endpoint.path, json=endpoint.payload)

    assert response.status_code == 500
    assert "psycopg2" not in response.text


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        pytest.param(BIAS, {**BIAS.payload, "sensitive_features": []}, id="bias-빈-보호속성"),
        pytest.param(BIAS, {k: v for k, v in BIAS.payload.items() if k != "audit_name"},
                     id="bias-감사명-누락"),
        pytest.param(FAIRNESS, {**FAIRNESS.payload, "model_s3_key": ""},
                     id="fairness-빈-모델키"),
        pytest.param(FAIRNESS, {**FAIRNESS.payload, "manual_threshold": 1.5},
                     id="fairness-범위밖-임계값"),
        pytest.param(COMPLIANCE, {**COMPLIANCE.payload, "regulation_mappings": []},
                     id="compliance-빈-조항목록"),
        pytest.param(COMPLIANCE, {**COMPLIANCE.payload, "self_check_answers": []},
                     id="compliance-빈-자가점검"),
        pytest.param(EXPLAINABILITY, {**EXPLAINABILITY.payload, "report_top_n": 0},
                     id="explainability-범위밖-top-n"),
        pytest.param(CHAT, {**CHAT.payload, "question": ""}, id="chat-빈-질문"),
        pytest.param(CHAT, {**CHAT.payload, "question": "가" * 2001}, id="chat-너무-긴-질문"),
    ],
)
def test_rejects_invalid_request(endpoint: Endpoint, payload: dict):
    """요청 스키마 위반은 서비스를 부르기 전에 422 로 막는다."""

    assert client.post(endpoint.path, json=payload).status_code == 422
