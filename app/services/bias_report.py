"""편향진단 리포트 생성 서비스.

공정성 감사 응답(`AuditRunResponse` — 집단 통계·7개 지표·성능)을 근거로 HTML
리포트를 만든다. 수치·표·그림은 코드가 삽입하고 서술 문단만 LLM 이 섹션별로
생성한다(판정 없이 값·격차만). 완성 HTML 을 S3 에 올리고 Key 를 돌려준다.

SHAP 리포트와 달리 분석 응답에 데이터가 이미 다 들어 있어 별도 payload 확장이
필요 없고, figure 만 리포트 단계에서 새로 생성한다.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.audit import AuditRunResponse
from app.schemas.bias_report import BiasReportRequest, BiasReportResponse
from app.schemas.fairness_internal import FairnessAnalyzeRequest
from app.services import bias_report_prompts
from app.services.bias_figures import generate_bias_figures
from app.services.bias_report_prompts import SYSTEM
from app.services.fairness_analysis import analyze_s3_request as analyze_fairness_s3
from app.services.llm import complete
from app.services.storage import upload_s3_object

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "bias_report.html.j2"


def _build_narratives(
    audit: AuditRunResponse,
    complete_fn: Callable[..., str],
) -> dict[str, str]:
    """섹션별로 LLM 을 호출해 서술 문단을 생성한다."""

    return {
        "overview_purpose": complete_fn(
            bias_report_prompts.overview_purpose_prompt(audit), system=SYSTEM
        ),
        "group_results": complete_fn(
            bias_report_prompts.group_results_prompt(audit), system=SYSTEM
        ),
        "metric_results": complete_fn(
            bias_report_prompts.metric_results_prompt(audit), system=SYSTEM
        ),
        "tradeoff": complete_fn(
            bias_report_prompts.tradeoff_prompt(audit), system=SYSTEM
        ),
        "overall_summary": complete_fn(
            bias_report_prompts.overall_summary_prompt(audit), system=SYSTEM
        ),
    }


def _render_html(context: dict[str, Any]) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = environment.get_template(TEMPLATE_NAME)
    return template.render(**context)


def generate_bias_report(request: BiasReportRequest) -> BiasReportResponse:
    """S3 파일로 공정성 감사를 실행하고 편향진단 HTML 리포트를 만들어 S3 에 올린다."""

    audit = analyze_fairness_s3(
        FairnessAnalyzeRequest(**request.model_dump()),
        include_report_meta=True,
    )

    narratives = _build_narratives(audit, complete)
    figures = generate_bias_figures(audit)
    generated_at = datetime.now(timezone.utc).isoformat()

    html = _render_html(
        {
            "meta": {
                "audit_id": request.audit_id,
                "audit_name": audit.audit_name,
                "generated_at": generated_at,
                "n_customers": audit.n_customers,
                "approval_rate": audit.approval_rate,
            },
            "audit": audit,
            "narratives": narratives,
            "figures": figures,
        }
    )

    run_id = datetime.now(timezone.utc).strftime("bias_%Y%m%dT%H%M%SZ")
    report_key = f"bias-reports/{request.audit_id}/{run_id}/report.html"

    with tempfile.TemporaryDirectory(prefix=f"bias_report_{request.audit_id}_") as tmp:
        html_path = Path(tmp) / "report.html"
        html_path.write_text(html, encoding="utf-8")
        upload_s3_object(html_path, report_key)

    return BiasReportResponse(
        audit_id=request.audit_id,
        report_s3_key=report_key,
        format="html",
        generated_at=generated_at,
    )
