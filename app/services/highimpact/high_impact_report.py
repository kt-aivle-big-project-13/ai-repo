"""고영향 AI 사전진단 보고서 생성 서비스."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    select_autoescape,
)

from app.schemas.highimpact.high_impact_report import (
    HighImpactReportRequest,
    HighImpactReportResponse,
)
from app.services.docx_common import DocxGenerationError
from app.services.highimpact.high_impact_report_docx import (
    render_high_impact_report_to_docx,
)
from app.services.report.report_pdf import (
    PdfGenerationError,
    render_html_to_pdf,
)
from app.services.storage import upload_s3_object


TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
TEMPLATE_NAME = "high_impact_report.html.j2"

QUESTION_ORDER = {
    "GATE_01": 1,
    "GATE_02": 2,
    "A_01": 3,
    "A_02": 4,
    "A_03": 5,
    "B_01": 6,
    "B_02": 7,
    "B_03": 8,
}


class HighImpactReportGenerationError(RuntimeError):
    """고영향 AI 사전진단 보고서 생성에 실패한 경우."""


def _build_decision_reason(
    request: HighImpactReportRequest,
) -> str:
    """저장된 응답과 점수를 이용해 고정된 판정 근거를 만든다."""

    if request.condition_met:
        matched_count = sum(
            answer.answer
            for answer in request.answers
            if answer.group == "GATE"
        )

        return (
            f"정성 게이트 문항 중 {matched_count}개 문항에 "
            "“예”로 응답하여 고영향 AI로 판정되었습니다."
        )

    if request.result == "HIGH_IMPACT":
        return (
            f"정량 배점 결과 A그룹 {request.group_a_score}점, "
            f"B그룹 {request.group_b_score}점으로 총 "
            f"{request.total_score}점이 산정되었습니다. "
            "판정 기준인 4점 이상을 충족하여 고영향 AI로 "
            "판정되었습니다."
        )

    return (
        f"정량 배점 결과 A그룹 {request.group_a_score}점, "
        f"B그룹 {request.group_b_score}점으로 총 "
        f"{request.total_score}점이 산정되었습니다. "
        "판정 기준인 4점에 미달하여 고영향 AI 분류 요건에 "
        "해당하지 않습니다."
    )


def _build_html_context(
    request: HighImpactReportRequest,
) -> dict[str, Any]:
    """HTML 템플릿에 전달할 정형 데이터를 구성한다."""

    ordered_answers = sorted(
        request.answers,
        key=lambda answer: QUESTION_ORDER[answer.question_code],
    )

    return {
        "request": request,
        "assessed_at": request.assessed_at.strftime(
            "%Y-%m-%d %H:%M:%S %z"
        ),
        "result_label": (
            "고영향 AI"
            if request.result == "HIGH_IMPACT"
            else "고영향 AI 미해당"
        ),
        "decision_reason": _build_decision_reason(request),
        "qualitative_answers": [
            answer
            for answer in ordered_answers
            if answer.stage == "QUALITATIVE"
        ],
        "quantitative_answers": [
            answer
            for answer in ordered_answers
            if answer.stage == "QUANTITATIVE"
        ],
    }


def render_high_impact_report_html(
    request: HighImpactReportRequest,
) -> str:
    """사전진단 데이터를 고정 HTML 템플릿으로 렌더링한다."""

    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
    )
    template = environment.get_template(TEMPLATE_NAME)

    try:
        return template.render(
            **_build_html_context(request),
        )
    except (KeyError, TypeError, ValueError) as exception:
        raise HighImpactReportGenerationError(
            "고영향 AI 사전진단 HTML 보고서 생성에 실패했습니다."
        ) from exception


def generate_high_impact_report(
    request: HighImpactReportRequest,
) -> HighImpactReportResponse:
    """사전진단 PDF·Word 보고서를 생성해 S3에 업로드한다."""

    generated_at = datetime.now(timezone.utc)
    decision_reason = _build_decision_reason(request)
    html = render_high_impact_report_html(request)

    run_id = (
        "high_impact_"
        f"{generated_at.strftime('%Y%m%dT%H%M%SZ')}_"
        f"{uuid4().hex}"
    )
    report_prefix = (
        f"high-impact-reports/{request.audit_id}/"
        f"{request.assessment_id}/{run_id}"
    )

    pdf_report_key = f"{report_prefix}/report.pdf"
    word_report_key = f"{report_prefix}/report.docx"

    try:
        with tempfile.TemporaryDirectory(
            prefix=(
                f"high_impact_report_"
                f"{request.assessment_id}_"
            )
        ) as temporary_directory:
            temporary_path = Path(temporary_directory)
            html_path = temporary_path / "report.html"
            pdf_path = temporary_path / "report.pdf"
            docx_path = temporary_path / "report.docx"

            html_path.write_text(
                html,
                encoding="utf-8",
            )

            render_html_to_pdf(
                html_path,
                pdf_path,
            )
            render_high_impact_report_to_docx(
                request=request,
                decision_reason=decision_reason,
                docx_path=docx_path,
            )

            upload_s3_object(
                pdf_path,
                pdf_report_key,
            )
            upload_s3_object(
                docx_path,
                word_report_key,
            )
    except (
        DocxGenerationError,
        PdfGenerationError,
        OSError,
    ) as exception:
        raise HighImpactReportGenerationError(
            "고영향 AI 사전진단 PDF·Word 보고서 생성에 "
            "실패했습니다."
        ) from exception

    return HighImpactReportResponse(
        audit_id=request.audit_id,
        assessment_id=request.assessment_id,
        pdf_report_s3_key=pdf_report_key,
        word_report_s3_key=word_report_key,
        generated_at=generated_at,
    )