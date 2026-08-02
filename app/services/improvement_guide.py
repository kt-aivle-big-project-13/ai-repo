"""개선 권고 가이드 생성 서비스.

규제준수 판정서와 같이 S3 의 모델·데이터를 읽지 않는다. 조치가 필요한 항목은 백엔드에서
임계값 판정이 끝난 뒤 넘어오므로, 우선순위를 매기고 서술을 붙여 조판만 한다.

우선순위는 코드가 정한다. LLM 이 바꾸지 못하게 프롬프트에도 명시했다.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.improvement_guide import (
    ImprovementGuideRequest,
    ImprovementGuideResponse,
)
from app.schemas.report_narrative import build_narratives
from app.services import improvement_guide_prompts
from app.services.improvement_guide_docx import render_improvement_guide_to_docx
from app.services.improvement_guide_prompts import SYSTEM
from app.services.llm import complete
from app.services.report_pdf import render_html_to_pdf
from app.services.storage import upload_s3_object

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "improvement_guide.html.j2"

# 섹션 서술을 챗봇 근거로 넘길 때 쓰는 목차 제목. 리포트 목차는 이 저장소가 소유하는
# 정보라, 키만이 아니라 제목까지 함께 반환한다.
NARRATIVE_TITLES = {
    "overview": "1. 개선 개요",
    "regulation": "3. 규제 준수 개선",
    "fairness": "4. 공정성 개선",
    "explainability": "5. 설명가능성 개선",
    "follow_up": "6. 이행 점검 항목",
}

PRIORITY_LABELS = {"HIGH": "높음", "MEDIUM": "중간", "LOW": "낮음"}

# 심각도 순서는 공정성 PASS < REVIEW < FAIL, 설명가능성 PASS < WARNING < REVIEW 다.
# 법령 미준수는 법적 의무 미이행이라 항상 높음으로 둔다.
FAIRNESS_PRIORITY = {"FAIL": "HIGH", "REVIEW": "MEDIUM"}
EXPLAINABILITY_PRIORITY = {"REVIEW": "MEDIUM", "WARNING": "LOW"}


def build_actions(request: ImprovementGuideRequest) -> list[dict[str, Any]]:
    """조치 항목에 우선순위를 매겨 한 목록으로 모은다."""

    actions: list[dict[str, Any]] = []

    for gap in request.compliance_gaps:
        actions.append(
            {
                "priority": "HIGH",
                "area": "규제 준수",
                "target": f"{gap.law_name} {gap.article_no}",
                "detail": gap.summary or gap.evidence,
            }
        )

    for gap in request.self_check_gaps:
        actions.append(
            {
                "priority": "HIGH",
                "area": "규제 준수",
                "target": gap.label,
                "detail": "자가점검 미충족 항목",
            }
        )

    for finding in request.fairness_findings:
        actions.append(
            {
                "priority": FAIRNESS_PRIORITY.get(finding.status, "MEDIUM"),
                "area": "공정성",
                "target": f"{finding.attribute} · {finding.metric_code}",
                "detail": f"관측값 {finding.value} / 임계값 {finding.threshold}"
                f" ({finding.status})",
            }
        )

    for finding in request.explainability_findings:
        actions.append(
            {
                "priority": EXPLAINABILITY_PRIORITY.get(finding.status, "MEDIUM"),
                "area": "설명가능성",
                "target": finding.metric_code,
                "detail": f"관측값 {finding.value} / 임계값 {finding.threshold}"
                f" ({finding.status})",
            }
        )

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    actions.sort(key=lambda action: order[action["priority"]])

    return actions


def count_priorities(actions: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    for action in actions:
        counts[action["priority"]] += 1

    counts["TOTAL"] = len(actions)
    return counts


def _build_narratives(
    request: ImprovementGuideRequest,
    counts: dict[str, int],
    complete_fn: Callable[..., str],
) -> dict[str, str]:
    return {
        "overview": complete_fn(
            improvement_guide_prompts.overview_prompt(request, counts),
            system=SYSTEM,
        ),
        "regulation": complete_fn(
            improvement_guide_prompts.regulation_prompt(request), system=SYSTEM
        ),
        "fairness": complete_fn(
            improvement_guide_prompts.fairness_prompt(request), system=SYSTEM
        ),
        "explainability": complete_fn(
            improvement_guide_prompts.explainability_prompt(request), system=SYSTEM
        ),
        "follow_up": complete_fn(
            improvement_guide_prompts.follow_up_prompt(request, counts),
            system=SYSTEM,
        ),
    }


def _render_html(context: dict[str, Any]) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = environment.get_template(TEMPLATE_NAME)
    return template.render(**context)


def generate_improvement_guide(
    request: ImprovementGuideRequest,
) -> ImprovementGuideResponse:
    """조치가 필요한 항목으로 개선 권고 가이드를 만들어 S3 에 올린다."""

    actions = build_actions(request)
    counts = count_priorities(actions)
    narratives = _build_narratives(request, counts, complete)
    generated_at = datetime.now(timezone.utc).isoformat()

    context = {
        "meta": {
            "audit_id": request.audit_id,
            "audit_name": request.audit_name,
            "model_name": request.model_name,
            "generated_at": generated_at,
        },
        "actions": actions,
        "counts": counts,
        "priority_labels": PRIORITY_LABELS,
        "narratives": narratives,
        "request": request,
    }

    html = _render_html(context)

    run_id = (
        f"improvement_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"_{uuid4().hex}"
    )
    prefix = f"improvement-guides/{request.audit_id}/{run_id}"
    report_key = f"{prefix}/report.html"
    pdf_report_key = f"{prefix}/report.pdf"
    word_report_key = f"{prefix}/report.docx"

    with tempfile.TemporaryDirectory(
        prefix=f"improvement_guide_{request.audit_id}_"
    ) as tmp:
        html_path = Path(tmp) / "report.html"
        pdf_path = Path(tmp) / "report.pdf"
        docx_path = Path(tmp) / "report.docx"
        html_path.write_text(html, encoding="utf-8")

        render_html_to_pdf(html_path, pdf_path)

        render_improvement_guide_to_docx(
            meta=context["meta"],
            request=request,
            actions=actions,
            counts=counts,
            narratives=narratives,
            docx_path=docx_path,
        )

        # 렌더가 모두 끝난 뒤 업로드해, 실패 시 S3 에 일부만 남지 않게 한다.
        upload_s3_object(html_path, report_key)
        upload_s3_object(pdf_path, pdf_report_key)
        upload_s3_object(docx_path, word_report_key)

    return ImprovementGuideResponse(
        audit_id=request.audit_id,
        report_s3_key=report_key,
        pdf_report_s3_key=pdf_report_key,
        word_report_s3_key=word_report_key,
        format="html",
        high_priority_count=counts["HIGH"],
        medium_priority_count=counts["MEDIUM"],
        low_priority_count=counts["LOW"],
        generated_at=generated_at,
        narratives=build_narratives(narratives, NARRATIVE_TITLES),
    )
