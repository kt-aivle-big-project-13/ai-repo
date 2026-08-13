"""편향진단 리포트 생성 서비스.

공정성 감사 응답(`AuditRunResponse` — 집단 통계·7개 지표·성능)을 근거로 HTML
리포트를 만든다. 수치·표·그림은 코드가 삽입하고 서술 문단만 LLM 이 섹션별로
생성한다(판정 없이 값·격차만). 완성 HTML 을 S3 에 올리고 Key 를 돌려준다.

SHAP 리포트와 달리 분석 응답에 데이터가 이미 다 들어 있어 별도 payload 확장이
필요 없고, figure 만 리포트 단계에서 새로 생성한다.
"""

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.audit import AuditRunResponse
from app.schemas.bias.bias_report import BiasReportRequest, BiasReportResponse
from app.schemas.report.report_narrative import build_narratives
from app.schemas.fairness.fairness_internal import FairnessAnalyzeRequest
from app.services.bias import bias_report_prompts
from app.services.bias.bias_figures import generate_bias_figures
from app.services.bias.bias_report_docx import render_bias_report_to_docx
from app.services.bias.bias_report_prompts import SYSTEM
from app.services.fairness.fairness_analysis import ARTIFACT_PREFIX_ROOT as ANALYSIS_PREFIX_ROOT
from app.services.fairness.fairness_analysis import RESULT_FILE_NAME
from app.services.fairness.fairness_analysis import analyze_s3_request as analyze_fairness_s3
from app.services.llm import complete
from app.services.report.report_pdf import render_html_to_pdf
from app.services.storage import (
    S3ConfigurationError,
    S3DownloadError,
    download_s3_object,
    find_latest_prefix,
    is_run_prefix_of,
    upload_s3_object,
)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
TEMPLATE_NAME = "bias_report.html.j2"

# 섹션 서술을 챗봇 근거로 넘길 때 쓰는 목차 제목. 리포트 목차는 이 저장소가 소유하는
# 정보라, 키만이 아니라 제목까지 함께 반환한다.
NARRATIVE_TITLES = {
    "overview_purpose": "1. 감사 개요",
    "group_results": "4. 집단별 결과 (기술통계)",
    "metric_results": "5. 공정성 지표 결과",
    "tradeoff": "6. 성능–공정성 트레이드오프",
    "overall_summary": "7. 종합 (기술 요약)",
}


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


def _resolve_analysis_prefix(request: BiasReportRequest) -> str | None:
    """재사용할 감사 결과 프리픽스를 정한다.

    요청으로 받은 값은 그대로 믿지 않고 이 감사의 결과 범위인지 확인한다. 다른
    감사(`fairness/43/...`)를 가리키는 값을 받아 읽으면 남의 감사 결과가 이
    리포트에 실리기 때문이다. 범위를 벗어나면 None 을 돌려줘 감사를 직접
    실행하는 경로로 넘긴다. 설명가능성 리포트와 같은 방식이다
    (`app/services/report/report.py`).
    """

    base = f"{ANALYSIS_PREFIX_ROOT}/{request.audit_id}"

    if not request.analysis_prefix:
        return find_latest_prefix(base)

    if not is_run_prefix_of(request.analysis_prefix, base):
        logger.warning(
            "요청한 결과 프리픽스가 이 감사의 범위를 벗어나 무시합니다: "
            "audit_id=%s, prefix=%s",
            request.audit_id,
            request.analysis_prefix,
        )
        return None

    return request.analysis_prefix


def _reuse_prior_audit(request: BiasReportRequest) -> AuditRunResponse | None:
    """직전 공정성 분석이 S3 에 남긴 결과를 재사용한다.

    `/internal/v1/fairness/analyze` 가 감사 결과를 통째로 남기므로, 같은 입력으로
    감사를 다시 실행할 이유가 없다.

    재사용할 결과를 못 찾거나 읽지 못하면 None 을 돌려준다 — 호출부가 직접 감사를
    실행하는 경로로 넘어가 리포트 생성 자체는 실패하지 않게 하기 위함이다.
    """

    prefix = _resolve_analysis_prefix(request)
    if not prefix:
        return None

    try:
        with tempfile.TemporaryDirectory(
            prefix=f"bias_reuse_{request.audit_id}_"
        ) as tmp:
            path = download_s3_object(
                f"{prefix}/{RESULT_FILE_NAME}",
                Path(tmp) / RESULT_FILE_NAME,
            )
            return AuditRunResponse.model_validate_json(
                path.read_text(encoding="utf-8")
            )
    except (S3DownloadError, S3ConfigurationError, OSError, ValueError) as exception:
        logger.warning(
            "공정성 감사 결과 재사용 실패, 감사를 직접 실행합니다: audit_id=%s, prefix=%s (%s)",
            request.audit_id,
            prefix,
            exception,
        )
        return None


def _run_audit_from_source(request: BiasReportRequest) -> AuditRunResponse:
    """재사용할 결과가 없을 때 S3 파일로 감사를 직접 실행한다."""

    payload = request.model_dump(exclude={"analysis_prefix"})
    return analyze_fairness_s3(
        FairnessAnalyzeRequest(**payload),
        include_report_meta=True,
    )


def generate_bias_report(request: BiasReportRequest) -> BiasReportResponse:
    """공정성 감사 결과로 편향진단 HTML 리포트를 만들어 S3 에 올린다.

    직전 분석이 S3 에 남긴 결과를 재사용하고, 없을 때만 감사를 직접 실행한다.
    """

    audit = _reuse_prior_audit(request) or _run_audit_from_source(request)

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

    # 초 단위 시각만으로는 같은 초의 동시/재시도 요청이 같은 S3 키를 덮어쓰므로,
    # 충돌 불가능한 uuid 를 붙여 실행마다 고유한 prefix 를 만든다.
    run_id = (
        f"bias_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"_{uuid4().hex}"
    )
    prefix = f"bias-reports/{request.audit_id}/{run_id}"
    report_key = f"{prefix}/report.html"
    pdf_report_key = f"{prefix}/report.pdf"
    word_report_key = f"{prefix}/report.docx"

    with tempfile.TemporaryDirectory(prefix=f"bias_report_{request.audit_id}_") as tmp:
        html_path = Path(tmp) / "report.html"
        pdf_path = Path(tmp) / "report.pdf"
        docx_path = Path(tmp) / "report.docx"
        html_path.write_text(html, encoding="utf-8")

        # HTML 을 Chromium(Playwright)으로 렌더해 서식 있는 PDF 로 변환한다.
        render_html_to_pdf(html_path, pdf_path)

        # Word 는 HTML 변환이 아니라 같은 데이터로 다시 조판한다(HTML→DOCX 변환은
        # 표·그림 서식이 깨져서 설명가능성 리포트도 같은 방식을 쓴다).
        render_bias_report_to_docx(
            meta={
                "audit_id": request.audit_id,
                "audit_name": audit.audit_name,
                "generated_at": generated_at,
                "n_customers": audit.n_customers,
                "approval_rate": audit.approval_rate,
            },
            audit=audit,
            narratives=narratives,
            figures=figures,
            docx_path=docx_path,
        )

        upload_s3_object(html_path, report_key)
        upload_s3_object(pdf_path, pdf_report_key)
        upload_s3_object(docx_path, word_report_key)

    return BiasReportResponse(
        audit_id=request.audit_id,
        report_s3_key=report_key,
        pdf_report_s3_key=pdf_report_key,
        word_report_s3_key=word_report_key,
        format="html",
        generated_at=generated_at,
        narratives=build_narratives(narratives, NARRATIVE_TITLES),
    )
