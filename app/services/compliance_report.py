"""규제준수 판정서 생성 서비스.

편향·설명가능성 리포트와 달리 S3 의 모델·데이터를 읽지 않는다. 판정 근거(자가점검
응답·법령 매핑)와 참고용 감사 요약이 요청에 담겨 오므로, 조판과 섹션별 LLM 서술만
수행한다. 공정성 재계산이 없어 훨씬 빠르다.

판정 자체도 여기서 내리지 않는다. 조항별 준수/미준수는 백엔드가 자가점검 응답으로
이미 정해 보내고, 이 서비스는 집계와 서술만 한다.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.compliance_report import (
    ComplianceReportRequest,
    ComplianceReportResponse,
)
from app.services import compliance_report_prompts
from app.services.compliance_report_docx import render_compliance_report_to_docx
from app.services.compliance_report_prompts import SYSTEM
from app.services.llm import complete
from app.services.report_pdf import render_html_to_pdf
from app.services.storage import upload_s3_object

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "compliance_report.html.j2"


def count_verdicts(request: ComplianceReportRequest) -> dict[str, int]:
    """조항별 판정을 집계한다. 판정을 새로 내리지 않고 세기만 한다."""

    counts = {"COMPLIANT": 0, "NON_COMPLIANT": 0, "PENDING": 0}

    for mapping in request.regulation_mappings:
        counts[mapping.compliance] += 1

    counts["TOTAL"] = len(request.regulation_mappings)
    return counts


def _build_narratives(
    request: ComplianceReportRequest,
    complete_fn: Callable[..., str],
) -> dict[str, str]:
    """섹션별로 LLM 을 호출해 서술 문단을 생성한다."""

    return {
        "overview": complete_fn(
            compliance_report_prompts.overview_prompt(request), system=SYSTEM
        ),
        "verdict_summary": complete_fn(
            compliance_report_prompts.verdict_summary_prompt(request), system=SYSTEM
        ),
        "non_compliance": complete_fn(
            compliance_report_prompts.non_compliance_prompt(request), system=SYSTEM
        ),
        "limitation": complete_fn(
            compliance_report_prompts.limitation_prompt(request), system=SYSTEM
        ),
    }


def _render_html(context: dict[str, Any]) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = environment.get_template(TEMPLATE_NAME)
    return template.render(**context)


def generate_compliance_report(
    request: ComplianceReportRequest,
) -> ComplianceReportResponse:
    """자가점검 기반 규제준수 판정서를 만들어 S3 에 올린다."""

    counts = count_verdicts(request)
    narratives = _build_narratives(request, complete)
    generated_at = datetime.now(timezone.utc).isoformat()

    non_compliant = [
        mapping
        for mapping in request.regulation_mappings
        if mapping.compliance == "NON_COMPLIANT"
    ]

    context = {
        "meta": {
            "audit_id": request.audit_id,
            "audit_name": request.audit_name,
            "model_name": request.model_name,
            "generated_at": generated_at,
        },
        "counts": counts,
        "narratives": narratives,
        "self_check_answers": request.self_check_answers,
        "regulation_mappings": request.regulation_mappings,
        "non_compliant": non_compliant,
        "audit_reference": request.audit_reference,
    }

    html = _render_html(context)

    # 편향·설명가능성 리포트와 같은 규칙으로 실행마다 고유한 prefix 를 만든다.
    run_id = (
        f"compliance_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"_{uuid4().hex}"
    )
    prefix = f"compliance-reports/{request.audit_id}/{run_id}"
    report_key = f"{prefix}/report.html"
    pdf_report_key = f"{prefix}/report.pdf"
    word_report_key = f"{prefix}/report.docx"

    with tempfile.TemporaryDirectory(
        prefix=f"compliance_report_{request.audit_id}_"
    ) as tmp:
        html_path = Path(tmp) / "report.html"
        pdf_path = Path(tmp) / "report.pdf"
        docx_path = Path(tmp) / "report.docx"
        html_path.write_text(html, encoding="utf-8")

        render_html_to_pdf(html_path, pdf_path)

        render_compliance_report_to_docx(
            meta=context["meta"],
            request=request,
            counts=counts,
            narratives=narratives,
            docx_path=docx_path,
        )

        # 렌더가 모두 끝난 뒤 업로드해, 실패 시 S3 에 일부만 남지 않게 한다.
        upload_s3_object(html_path, report_key)
        upload_s3_object(pdf_path, pdf_report_key)
        upload_s3_object(docx_path, word_report_key)

    return ComplianceReportResponse(
        audit_id=request.audit_id,
        report_s3_key=report_key,
        pdf_report_s3_key=pdf_report_key,
        word_report_s3_key=word_report_key,
        format="html",
        compliant_count=counts["COMPLIANT"],
        non_compliant_count=counts["NON_COMPLIANT"],
        pending_count=counts["PENDING"],
        generated_at=generated_at,
    )
