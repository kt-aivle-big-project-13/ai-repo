"""설명가능성 HTML 리포트 PDF 변환 테스트."""

from pathlib import Path

import pytest

from app.services.report.report_pdf import render_html_to_pdf


def test_render_html_to_pdf_creates_pdf(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "report.html"
    pdf_path = tmp_path / "report.pdf"

    html_path.write_text(
        """
        <!doctype html>
        <html lang="ko">
        <head>
          <meta charset="utf-8">
          <style>
            body { font-family: sans-serif; }
            h1 { color: #12875a; }
          </style>
        </head>
        <body>
          <h1>설명가능성 감사 리포트</h1>
          <p>한글 PDF 변환 테스트</p>
        </body>
        </html>
        """,
        encoding="utf-8",
    )

    result = render_html_to_pdf(html_path, pdf_path)

    assert result == pdf_path.resolve()
    assert pdf_path.stat().st_size > 1000
    assert pdf_path.read_bytes().startswith(b"%PDF-")


def test_render_html_to_pdf_rejects_missing_html(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "missing.html"
    pdf_path = tmp_path / "report.pdf"

    with pytest.raises(FileNotFoundError):
        render_html_to_pdf(html_path, pdf_path)