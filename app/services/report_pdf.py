"""HTML 설명가능성 리포트를 PDF로 변환한다."""

from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


class PdfGenerationError(RuntimeError):
    """HTML 리포트의 PDF 변환에 실패한 경우."""


def render_html_to_pdf(
    html_path: Path,
    pdf_path: Path,
) -> Path:
    """HTML 파일을 Chromium으로 렌더링해 A4 PDF로 저장한다."""

    source = html_path.resolve()
    destination = pdf_path.resolve()

    if not source.is_file():
        raise FileNotFoundError(
            f"PDF 변환 대상 HTML 파일이 없습니다: {source}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()

            try:
                page = browser.new_page()
                page.goto(source.as_uri(), wait_until="load")
                page.emulate_media(media="screen")
                page.pdf(
                    path=str(destination),
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                )
            finally:
                browser.close()
    except PlaywrightError as exception:
        raise PdfGenerationError(
            "설명가능성 리포트 PDF 변환에 실패했습니다."
        ) from exception

    if not destination.is_file() or destination.stat().st_size == 0:
        raise PdfGenerationError(
            "설명가능성 리포트 PDF 파일이 생성되지 않았습니다."
        )

    return destination