"""고영향 AI 사전진단 HTML 보고서 테스트."""
from pathlib import Path

from app.schemas.highimpact.high_impact_report import HighImpactReportRequest
from app.services.highimpact import high_impact_report as report_service
from app.services.highimpact.high_impact_report import (
    render_high_impact_report_html,
)
from app.services.highimpact.high_impact_report_docx import (
    render_high_impact_report_to_docx,
)
from app.services.report.report_pdf import render_html_to_pdf

def _gate_answer(
    question_code: str,
    question_text: str,
    answer: bool,
) -> dict:
    return {
        "question_code": question_code,
        "question_text": question_text,
        "stage": "QUALITATIVE",
        "group": "GATE",
        "answer": answer,
        "weight": 0,
        "score": 0,
    }


def _quantitative_answer(
    question_code: str,
    group: str,
    answer: bool,
) -> dict:
    weight = 2 if group == "A" else 1

    return {
        "question_code": question_code,
        "question_text": f"{question_code} 진단 문항",
        "stage": "QUANTITATIVE",
        "group": group,
        "answer": answer,
        "weight": weight,
        "score": weight if answer else 0,
    }


def _qualitative_request() -> HighImpactReportRequest:
    return HighImpactReportRequest(
        audit_id=8,
        assessment_id=10,
        audit_name="신용평가 모델 감사",
        model_name="신용평가 모델",
        model_version="1.0",
        assessed_at="2026-07-31T10:00:00+09:00",
        condition_met=True,
        group_a_score=0,
        group_b_score=0,
        total_score=0,
        result="HIGH_IMPACT",
        answers=[
            _gate_answer(
                "GATE_01",
                "국민의 생명과 기본권에 영향을 미치나요?",
                True,
            ),
            _gate_answer(
                "GATE_02",
                "중요 의사결정에 활용되나요?",
                False,
            ),
        ],
    )


def _quantitative_request() -> HighImpactReportRequest:
    return HighImpactReportRequest(
        audit_id=8,
        assessment_id=11,
        audit_name="신용평가 모델 감사",
        model_name="신용평가 모델",
        model_version="1.0",
        assessed_at="2026-07-31T10:00:00+09:00",
        condition_met=False,
        group_a_score=4,
        group_b_score=0,
        total_score=4,
        result="HIGH_IMPACT",
        answers=[
            _gate_answer("GATE_01", "정성 문항 1", False),
            _gate_answer("GATE_02", "정성 문항 2", False),
            _quantitative_answer("A_01", "A", True),
            _quantitative_answer("A_02", "A", True),
            _quantitative_answer("A_03", "A", False),
            _quantitative_answer("B_01", "B", False),
            _quantitative_answer("B_02", "B", False),
            _quantitative_answer("B_03", "B", False),
        ],
    )


def test_renders_qualitative_high_impact_report() -> None:
    html = render_high_impact_report_html(
        _qualitative_request()
    )

    assert "<!DOCTYPE html>" in html
    assert "고영향 AI 사전진단 보고서" in html
    assert "정성 게이트 문항 중 1개 문항" in html
    assert "국민의 생명과 기본권에 영향을 미치나요?" in html
    assert "정량 배점 응답" not in html


def test_renders_quantitative_high_impact_report() -> None:
    html = render_high_impact_report_html(
        _quantitative_request()
    )

    assert "정량 배점 응답" in html
    assert "A그룹 4점" in html
    assert "총 4점" in html
    assert "판정 기준인 4점 이상" in html
    assert "A_01" in html
    assert "B_03" in html


def test_escapes_user_provided_text() -> None:
    request = _qualitative_request()
    request.audit_name = "<script>alert('xss')</script>"

    html = render_high_impact_report_html(request)

    assert "<script>alert('xss')</script>" not in html
    assert "&lt;script&gt;" in html

def test_generates_and_uploads_pdf_and_word(
    monkeypatch,
) -> None:
    uploaded_files: dict[str, bytes] = {}

    def fake_render_html_to_pdf(
        html_path: Path,
        pdf_path: Path,
    ) -> Path:
        assert html_path.read_text(
            encoding="utf-8"
        ).startswith("<!DOCTYPE html>")

        pdf_path.write_bytes(b"%PDF-high-impact-test")
        return pdf_path.resolve()

    def fake_render_high_impact_report_to_docx(
        *,
        request,
        decision_reason,
        docx_path,
    ) -> Path:
        assert request.assessment_id == 11
        assert "총 4점" in decision_reason

        docx_path.write_bytes(b"PK-high-impact-docx-test")
        return docx_path.resolve()

    def fake_upload(
        source: Path,
        key: str,
    ) -> str:
        uploaded_files[key] = Path(source).read_bytes()
        return key

    monkeypatch.setattr(
        report_service,
        "render_html_to_pdf",
        fake_render_html_to_pdf,
    )
    monkeypatch.setattr(
        report_service,
        "render_high_impact_report_to_docx",
        fake_render_high_impact_report_to_docx,
    )
    monkeypatch.setattr(
        report_service,
        "upload_s3_object",
        fake_upload,
    )

    response = report_service.generate_high_impact_report(
        _quantitative_request()
    )

    assert response.audit_id == 8
    assert response.assessment_id == 11
    assert response.pdf_report_s3_key.endswith(
        "/report.pdf"
    )
    assert response.word_report_s3_key.endswith(
        "/report.docx"
    )
    assert uploaded_files[
        response.pdf_report_s3_key
    ].startswith(b"%PDF-")
    assert uploaded_files[
        response.word_report_s3_key
    ].startswith(b"PK")

    assert all(
        not key.endswith(".html")
        for key in uploaded_files
    )

def test_creates_real_pdf_and_word_artifacts(
    tmp_path: Path,
) -> None:
    request = _quantitative_request()

    html_path = tmp_path / "report.html"
    pdf_path = tmp_path / "report.pdf"
    docx_path = tmp_path / "report.docx"

    html_path.write_text(
        render_high_impact_report_html(request),
        encoding="utf-8",
    )

    render_html_to_pdf(
        html_path,
        pdf_path,
    )
    render_high_impact_report_to_docx(
        request=request,
        decision_reason=(
            "정량 배점 결과 A그룹 4점, B그룹 0점으로 "
            "총 4점이 산정되었습니다. 판정 기준인 4점 이상을 "
            "충족하여 고영향 AI로 판정되었습니다."
        ),
        docx_path=docx_path,
    )

    assert pdf_path.stat().st_size > 1000
    assert pdf_path.read_bytes().startswith(b"%PDF-")

    assert docx_path.stat().st_size > 5000
    assert docx_path.read_bytes().startswith(b"PK")