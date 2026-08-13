"""설명가능성 리포트 생성 서비스.

SHAP 분석 산출물(#28 report payload·figure)을 근거로, 서술 문단은 LLM 이
섹션별로 생성하고 수치·표·그림은 코드가 삽입해 HTML 리포트를 만든다. 완성된
HTML 을 S3 에 올리고 그 Key 를 돌려준다.
"""

import base64
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.report.report import ReportRequest, ReportResponse
from app.schemas.report.report_narrative import build_narratives
from app.schemas.explainability.shap import ShapAnalysisRequest, ShapReport
from app.services.report import report_prompts
from app.services.llm import complete
from app.services.report.report_prompts import SYSTEM
from app.services.report.report_docx import (
    DocxGenerationError,
    render_report_to_docx,
)
from app.services.report.report_pdf import PdfGenerationError, render_html_to_pdf
from app.services.explainability.shap import analyze_local_files
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
TEMPLATE_NAME = "explainability_report.html.j2"

# SHAP 분석이 산출물을 올리는 위치. `app/services/explainability/shap.py` 가 쓰는
# 규칙과 같아야 한다.
ANALYSIS_PREFIX_ROOT = "explainability"

# 섹션 서술을 챗봇 근거로 넘길 때 쓰는 목차 제목. 리포트 목차는 이 저장소가 소유하는
# 정보라, 키만이 아니라 제목까지 함께 반환한다.
NARRATIVE_TITLES = {
    "overview_purpose": "1. 감사 개요",
    "results_summary": "1. 감사 개요 · 감사 결과 요약",
    "global_interpretation": "4. 전역 설명 분석",
    "reliability_summary": "5. 설명 신뢰성 검증",
    "overall_assessment": "7. 종합 평가",
}

# 리포트에 싣는 figure 파일과 캡션.
FIGURE_TITLES: dict[str, str] = {
    "explainability_dashboard.png": "설명가능성 KPI 대시보드",
    "global_shap_top20.png": "전역 SHAP 중요도 상위 20",
    "permutation_vs_shap.png": "SHAP vs 순열 중요도",
    "fidelity_distribution.png": "설명 충실성 분포",
    "sensitive_contribution.png": "민감변수 기여비율",
}


class ReportGenerationError(RuntimeError):
    """리포트 생성에 필요한 데이터가 없거나 조립에 실패한 경우."""


def _build_narratives(
    report: ShapReport,
    overall_status: str,
    complete_fn: Callable[..., str],
) -> dict[str, str]:
    """섹션별로 LLM 을 호출해 서술 문단을 생성한다."""

    return {
        "overview_purpose": complete_fn(
            report_prompts.overview_purpose_prompt(report), system=SYSTEM
        ),
        "results_summary": complete_fn(
            report_prompts.results_summary_prompt(report, overall_status), system=SYSTEM
        ),
        "global_interpretation": complete_fn(
            report_prompts.global_interpretation_prompt(report), system=SYSTEM
        ),
        "reliability_summary": complete_fn(
            report_prompts.reliability_summary_prompt(report), system=SYSTEM
        ),
        "overall_assessment": complete_fn(
            report_prompts.overall_assessment_prompt(report, overall_status),
            system=SYSTEM,
        ),
    }


def _load_figures(output_dir: Path) -> list[dict[str, str]]:
    """figure PNG 를 base64 data URI 로 읽어 자기완결 HTML 에 임베드한다."""

    figures_dir = output_dir / "figures"
    if not figures_dir.is_dir():
        return []

    figures: list[dict[str, str]] = []
    for name, title in FIGURE_TITLES.items():
        path = figures_dir / name
        if not path.is_file():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        figures.append(
            {
                "name": name,
                "title": title,
                "data_uri": f"data:image/png;base64,{encoded}",
            }
        )
    return figures


def _render_html(context: dict[str, Any]) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = environment.get_template(TEMPLATE_NAME)
    return template.render(**context)


def _resolve_analysis_prefix(request: ReportRequest) -> str | None:
    """재사용할 산출물 프리픽스를 정한다.

    요청으로 받은 값은 그대로 믿지 않고 이 감사의 산출물 범위인지 확인한다.
    다른 감사(`explainability/43/...`)를 가리키는 값을 받아 읽으면 남의 감사
    데이터가 이 리포트에 실리기 때문이다. 범위를 벗어나면 None 을 돌려줘
    산출물을 읽지 않고 분석을 직접 실행하는 경로로 넘긴다.
    """

    base = f"{ANALYSIS_PREFIX_ROOT}/{request.audit_id}"

    if not request.analysis_prefix:
        return find_latest_prefix(base)

    if not is_run_prefix_of(request.analysis_prefix, base):
        logger.warning(
            "요청한 산출물 프리픽스가 이 감사의 범위를 벗어나 무시합니다: "
            "audit_id=%s, prefix=%s",
            request.audit_id,
            request.analysis_prefix,
        )
        return None

    return request.analysis_prefix


def _reuse_prior_analysis(
    request: ReportRequest,
    output_dir: Path,
) -> tuple[ShapReport, str] | None:
    """직전 SHAP 분석이 S3 에 남긴 산출물을 내려받아 재사용한다.

    `/internal/v1/shap/analyze` 가 `include_report=True` 로 호출되면 실행 산출물
    전체가 `explainability/{audit_id}/{run_id}` 에 올라간다. 리포트에 필요한
    report payload 와 figure 가 그 안에 이미 있으므로, 같은 입력으로 파이프라인을
    다시 돌릴 이유가 없다.

    재사용할 산출물을 못 찾거나 내려받지 못하면 None 을 돌려준다 — 호출부가 직접
    분석하는 폴백 경로로 넘어가 리포트 생성 자체는 실패하지 않게 하기 위함이다.
    """

    prefix = _resolve_analysis_prefix(request)
    if not prefix:
        return None

    summary_dir = output_dir / "summary"
    try:
        payload_path = download_s3_object(
            f"{prefix}/summary/report_payload.json",
            summary_dir / "report_payload.json",
        )
        summary_path = download_s3_object(
            f"{prefix}/summary/explainability_summary.json",
            summary_dir / "explainability_summary.json",
        )
    except (S3DownloadError, S3ConfigurationError) as exception:
        logger.warning(
            "SHAP 산출물 재사용 실패, 분석을 직접 실행합니다: audit_id=%s, prefix=%s (%s)",
            request.audit_id,
            prefix,
            exception,
        )
        return None

    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        overall_status = json.loads(summary_path.read_text(encoding="utf-8"))[
            "overall_status"
        ]
        # 저장본은 상한까지 담겨 있으므로 이 리포트가 실을 만큼만 잘라 쓴다.
        payload["global_importance_top"] = payload["global_importance_top"][
            : request.report_top_n
        ]
        payload["sampling"] = {
            **payload["sampling"],
            "report_top_n": float(len(payload["global_importance_top"])),
        }
        report = ShapReport.model_validate(payload)
    except (KeyError, TypeError, ValueError) as exception:
        logger.warning(
            "SHAP 산출물 형식이 예상과 달라 분석을 직접 실행합니다: audit_id=%s, prefix=%s (%s)",
            request.audit_id,
            prefix,
            exception,
        )
        return None

    # figure 는 없으면 해당 그림만 빠질 뿐이라 개별 실패를 무시한다.
    for name in FIGURE_TITLES:
        try:
            download_s3_object(
                f"{prefix}/figures/{name}",
                output_dir / "figures" / name,
            )
        except (S3DownloadError, S3ConfigurationError):
            continue

    return report, overall_status


def _analyze_from_source(
    request: ReportRequest,
    temporary_path: Path,
    output_dir: Path,
) -> tuple[ShapReport, str]:
    """재사용할 산출물이 없을 때 모델·데이터셋을 내려받아 직접 분석한다."""

    shap_request = ShapAnalysisRequest(
        audit_id=request.audit_id,
        model_s3_key=request.model_s3_key,
        audit_dataset_s3_key=request.audit_dataset_s3_key,
        target_column=request.target_column,
        sensitive_features=request.sensitive_features,
        include_report=True,
        report_top_n=request.report_top_n,
    )

    model_suffix = Path(request.model_s3_key).suffix or ".json"
    dataset_suffix = Path(request.audit_dataset_s3_key).suffix or ".csv"
    model_path = temporary_path / f"model{model_suffix}"
    dataset_path = temporary_path / f"audit_dataset{dataset_suffix}"

    download_s3_object(request.model_s3_key, model_path)
    download_s3_object(request.audit_dataset_s3_key, dataset_path)

    analysis = analyze_local_files(
        request=shap_request,
        model_path=model_path,
        dataset_path=dataset_path,
        output_dir=output_dir,
    )

    if analysis.report is None:
        raise ReportGenerationError(
            "SHAP 분석에서 리포트 payload 를 얻지 못했습니다."
        )

    return analysis.report, analysis.overall_status


def generate_explainability_report(request: ReportRequest) -> ReportResponse:
    """SHAP 분석 결과로 HTML 리포트를 만들어 S3 에 올린다.

    직전 분석이 S3 에 남긴 산출물을 재사용하고, 없을 때만 분석을 직접 실행한다.
    """

    with tempfile.TemporaryDirectory(
        prefix=f"report_{request.audit_id}_"
    ) as temporary_directory:
        temporary_path = Path(temporary_directory)
        output_dir = temporary_path / "outputs"

        reused = _reuse_prior_analysis(request, output_dir)
        if reused is not None:
            report, overall_status = reused
        else:
            report, overall_status = _analyze_from_source(
                request, temporary_path, output_dir
            )

        narratives = _build_narratives(report, overall_status, complete)
        figures = _load_figures(output_dir)
        generated_at = datetime.now(timezone.utc).isoformat()

        meta = {
            "audit_id": request.audit_id,
            "generated_at": generated_at,
            "overall_status": overall_status,
            "model_file": report.manifest.get("model_file", ""),
            "data_file": report.manifest.get("data_file", ""),
        }

        html = _render_html(
            {
                "meta": meta,
                "report": report,
                "narratives": narratives,
                "figures": figures,
            }
        )

        run_id = report.manifest.get("run_id", f"report_{request.audit_id}")
        html_path = temporary_path / "report.html"
        pdf_path = temporary_path / "report.pdf"
        docx_path = temporary_path / "report.docx"
        html_path.write_text(html, encoding="utf-8")

        try:
            render_html_to_pdf(html_path, pdf_path)
        except (FileNotFoundError, PdfGenerationError) as exception:
            raise ReportGenerationError(
                "설명가능성 리포트 PDF 생성에 실패했습니다."
            ) from exception

        try:
            render_report_to_docx(
                meta=meta,
                report=report,
                narratives=narratives,
                figures=figures,
                docx_path=docx_path,
            )
        except (DocxGenerationError, OSError) as exception:
            raise ReportGenerationError(
                "설명가능성 Word 리포트 생성에 실패했습니다."
            ) from exception

        report_prefix = (
            f"explainability-reports/{request.audit_id}/{run_id}"
        )

        report_key = f"{report_prefix}/report.html"
        pdf_report_key = f"{report_prefix}/report.pdf"
        word_report_key = f"{report_prefix}/report.docx"

        upload_s3_object(html_path, report_key)
        upload_s3_object(pdf_path, pdf_report_key)
        upload_s3_object(docx_path, word_report_key)

        return ReportResponse(
            audit_id=request.audit_id,
            report_s3_key=report_key,
            pdf_report_s3_key=pdf_report_key,
            word_report_s3_key=word_report_key,
            format="html",
            overall_status=overall_status,
            generated_at=generated_at,
            narratives=build_narratives(narratives, NARRATIVE_TITLES),
        )
