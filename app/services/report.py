"""설명가능성 리포트 생성 서비스.

SHAP 분석 산출물(#28 report payload·figure)을 근거로, 서술 문단은 LLM 이
섹션별로 생성하고 수치·표·그림은 코드가 삽입해 HTML 리포트를 만든다. 완성된
HTML 을 S3 에 올리고 그 Key 를 돌려준다.
"""

import base64
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.report import ReportRequest, ReportResponse
from app.schemas.shap import ShapAnalysisRequest, ShapReport
from app.services import report_prompts
from app.services.llm import complete
from app.services.report_prompts import SYSTEM
from app.services.shap import analyze_local_files
from app.services.storage import download_s3_object, upload_s3_object

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "explainability_report.html.j2"

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


def generate_explainability_report(request: ReportRequest) -> ReportResponse:
    """S3 파일로 SHAP 분석을 실행하고 HTML 리포트를 만들어 S3 에 올린다."""

    shap_request = ShapAnalysisRequest(
        audit_id=request.audit_id,
        model_s3_key=request.model_s3_key,
        audit_dataset_s3_key=request.audit_dataset_s3_key,
        target_column=request.target_column,
        sensitive_features=request.sensitive_features,
        include_report=True,
        report_top_n=request.report_top_n,
    )

    with tempfile.TemporaryDirectory(
        prefix=f"report_{request.audit_id}_"
    ) as temporary_directory:
        temporary_path = Path(temporary_directory)

        model_suffix = Path(request.model_s3_key).suffix or ".json"
        dataset_suffix = Path(request.audit_dataset_s3_key).suffix or ".csv"
        model_path = temporary_path / f"model{model_suffix}"
        dataset_path = temporary_path / f"audit_dataset{dataset_suffix}"
        output_dir = temporary_path / "outputs"

        download_s3_object(request.model_s3_key, model_path)
        download_s3_object(request.audit_dataset_s3_key, dataset_path)

        analysis = analyze_local_files(
            request=shap_request,
            model_path=model_path,
            dataset_path=dataset_path,
            output_dir=output_dir,
        )

        report = analysis.report
        if report is None:
            raise ReportGenerationError(
                "SHAP 분석에서 리포트 payload 를 얻지 못했습니다."
            )

        narratives = _build_narratives(report, analysis.overall_status, complete)
        figures = _load_figures(output_dir)
        generated_at = datetime.now(timezone.utc).isoformat()

        html = _render_html(
            {
                "meta": {
                    "audit_id": request.audit_id,
                    "generated_at": generated_at,
                    "overall_status": analysis.overall_status,
                    "model_file": report.manifest.get("model_file", model_path.name),
                    "data_file": report.manifest.get("data_file", dataset_path.name),
                },
                "report": report,
                "narratives": narratives,
                "figures": figures,
            }
        )

        run_id = report.manifest.get("run_id", f"report_{request.audit_id}")
        html_path = temporary_path / "report.html"
        html_path.write_text(html, encoding="utf-8")

        report_key = (
            f"explainability-reports/{request.audit_id}/{run_id}/report.html"
        )
        upload_s3_object(html_path, report_key)

        return ReportResponse(
            audit_id=request.audit_id,
            report_s3_key=report_key,
            format="html",
            overall_status=analysis.overall_status,
            generated_at=generated_at,
        )
