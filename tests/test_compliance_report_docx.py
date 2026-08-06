"""규제준수 판정서 Word 렌더러 테스트.

생성 서비스 테스트는 DOCX 시그니처와 목차 일부만 확인한다. 여기서는 렌더러 자체의
조판 규칙(목차 구간, 표 머리글 반복, 행 분할 방지, A4 용지)과 데이터에 따라 나타나고
사라지는 조건부 절을 본다.
"""

from collections import Counter

from docx import Document
from docx.shared import Cm

from app.schemas.compliance.compliance_report import RegulationMapping
from app.services.compliance.compliance_report import count_verdicts
from app.services.compliance.compliance_report_docx import (
    render_compliance_report_to_docx,
)
from tests.test_compliance_report import _request

NARRATIVES = {
    "overview": "판정 개요 서술",
    "verdict_summary": "종합 판정 서술",
    "non_compliance": "미준수 상세 서술",
    "limitation": "판정 한계 서술",
}


def _meta() -> dict:
    return {
        "audit_id": 42,
        "audit_name": "테스트 감사",
        "model_name": "credit_model v1",
        "generated_at": "2026-08-06T00:00:00+00:00",
    }


def _render(tmp_path, request=None):
    request = request or _request()
    docx_path = tmp_path / "report.docx"

    render_compliance_report_to_docx(
        meta=_meta(),
        request=request,
        counts=count_verdicts(request),
        narratives=NARRATIVES,
        docx_path=docx_path,
    )

    return Document(str(docx_path))


def _paragraph_text(document) -> str:
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _table_text(document) -> str:
    return "\n".join(
        cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )


def _mapping(law_name: str, article_no: str, compliance: str) -> RegulationMapping:
    return RegulationMapping(
        law_name=law_name,
        article_no=article_no,
        content="조항 본문",
        compliance=compliance,
        evidence="자율점검 기반 자동 매칭",
    )


def _table_rows(document, headers: list[str]) -> list[list[str]]:
    """머리글이 일치하는 표의 본문 행을 돌려준다.

    문서 전체를 문자열로 합쳐 `in` 으로 보면 값이 엉뚱한 행에 들어가도 통과한다.
    """

    for table in document.tables:
        if [cell.text for cell in table.rows[0].cells] == headers:
            return [[cell.text for cell in row.cells] for row in table.rows[1:]]

    raise AssertionError(f"머리글이 {headers} 인 표를 찾지 못함")


def test_renders_all_sections(tmp_path):
    document = _render(tmp_path)
    text = _paragraph_text(document)

    for heading in (
        "1. 판정 개요",
        "2. 종합 판정 결과",
        "3. 자가점검 결과",
        "4. 조항별 준수 판정",
        "5. 미준수 항목 상세",
        "부록 · 판정 방법과 한계",
    ):
        assert heading in text, heading

    for narrative in NARRATIVES.values():
        assert narrative in text, narrative


def test_renders_verdicts_and_self_check_answers(tmp_path):
    table_text = _table_text(_render(tmp_path))

    # 조항별 판정
    assert "신용정보법" in table_text
    assert "제36조의2" in table_text
    assert "미준수" in table_text
    assert "준수" in table_text

    # 자가점검 응답
    assert "AI 심사 사실 사전 고지 여부" in table_text
    assert "예" in table_text
    assert "아니오" in table_text


def test_counts_match_request(tmp_path):
    """집계표 값이 요청 데이터와 일치한다. 판정을 새로 내리지 않는다.

    기대값은 `count_verdicts` 가 아니라 요청에서 직접 센다 — 집계 함수와 같은 값을
    비교하면 둘이 함께 틀려도 통과한다.
    """

    # 판정마다 개수를 다르게 둔다 — 기본 픽스처는 준수·미준수가 1건씩이라 두 값이
    # 서로 바뀌어도 표가 그대로고, PENDING 은 0 이라 어떤 값을 넣어도 맞는 것처럼 보인다.
    request = _request(regulation_mappings=[
        _mapping("신용정보법", "제36조의2", "NON_COMPLIANT"),
        _mapping("AI 기본법", "제27조", "COMPLIANT"),
        _mapping("AI 기본법", "제31조", "COMPLIANT"),
        _mapping("개인정보보호법", "제37조의2", "PENDING"),
    ])
    verdicts = Counter(mapping.compliance for mapping in request.regulation_mappings)

    rows = _table_rows(_render(tmp_path, request), ["판정", "조항 수"])

    assert rows == [
        ["준수 (COMPLIANT)", str(verdicts["COMPLIANT"])],
        ["미준수 (NON_COMPLIANT)", str(verdicts["NON_COMPLIANT"])],
        ["보류 (PENDING)", str(verdicts["PENDING"])],
        ["합계", str(len(request.regulation_mappings))],
    ]


def test_repeats_table_headers_and_prevents_row_split(tmp_path):
    """표가 페이지를 넘어가도 머리글이 반복되고 행이 쪼개지지 않아야 한다."""

    document = _render(tmp_path)

    assert document.tables, "표가 하나도 없음"

    for index, table in enumerate(document.tables):
        # 머리글 반복은 첫 행에만 필요하다.
        assert "tblHeader" in table.rows[0]._tr.xml, f"{index}번째 표 머리글"

        # 분할 방지는 본문 행에도 있어야 한다 — 머리글만 보면 본문 설정이 빠져도 통과한다.
        for row_index, row in enumerate(table.rows):
            assert "cantSplit" in row._tr.xml, f"{index}번째 표 {row_index}번째 행"


def test_uses_a4_page_size(tmp_path):
    # twips 로 저장되며 되읽을 때 반올림 오차가 있어 허용범위로 본다.
    section = _render(tmp_path).sections[0]

    assert abs(section.page_width - Cm(21.0)) < 1000
    assert abs(section.page_height - Cm(29.7)) < 1000


def test_omits_reference_section_without_audit_reference(tmp_path):
    """참고 감사 요약은 데이터가 있을 때만 나온다."""

    with_reference = _paragraph_text(_render(tmp_path))
    assert "6. 참고 · 감사 결과 요약" in with_reference

    without_reference = _paragraph_text(
        _render(tmp_path, _request(audit_reference=None))
    )
    assert "6. 참고 · 감사 결과 요약" not in without_reference

    # 판정 본문은 그대로 남아야 한다.
    assert "4. 조항별 준수 판정" in without_reference


def test_notes_when_nothing_is_non_compliant(tmp_path):
    """미준수 조항이 없으면 그 사실을 표시한다."""

    request = _request(
        regulation_mappings=[
            mapping
            for mapping in _request().regulation_mappings
            if mapping.compliance == "COMPLIANT"
        ]
    )

    text = _paragraph_text(_render(tmp_path, request))

    assert "미준수로 판정된 조항 없음" in text
