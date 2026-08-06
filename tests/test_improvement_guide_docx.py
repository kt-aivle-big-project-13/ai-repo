"""개선 권고 가이드 Word 렌더러 테스트.

생성 서비스 테스트는 DOCX 시그니처와 목차 일부만 확인한다. 여기서는 렌더러 자체의
조판 규칙(목차 구간, 표 머리글 반복, 행 분할 방지, A4 용지)과 조치 항목이 없을 때의
표시를 본다.
"""

from docx import Document
from docx.shared import Cm

from app.services.improvement.improvement_guide import (
    build_actions,
    count_priorities,
)
from app.services.improvement.improvement_guide_docx import (
    render_improvement_guide_to_docx,
)
from tests.test_improvement_guide import _request

NARRATIVES = {
    "overview": "개선 개요 서술",
    "regulation": "규제 준수 개선 서술",
    "fairness": "공정성 개선 서술",
    "explainability": "설명가능성 개선 서술",
    "follow_up": "이행 점검 서술",
    "recommendations": "참고 권고 서술",
}


def _meta() -> dict:
    return {
        "audit_id": 88,
        "audit_name": "테스트 감사",
        "model_name": "credit_model v1",
        "generated_at": "2026-08-06T00:00:00+00:00",
    }


def _render(tmp_path, request=None):
    request = request or _request()
    actions = build_actions(request)
    docx_path = tmp_path / "report.docx"

    render_improvement_guide_to_docx(
        meta=_meta(),
        request=request,
        actions=actions,
        counts=count_priorities(actions),
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


def test_renders_all_sections(tmp_path):
    document = _render(tmp_path)
    text = _paragraph_text(document)

    for heading in (
        "1. 개선 개요",
        "2. 개선 과제 요약",
        "3. 규제 준수 개선",
        "4. 공정성 개선",
        "5. 설명가능성 개선",
        "6. 이행 점검 항목",
        "부록 · 권고 범위와 한계",
    ):
        assert heading in text, heading

    for narrative in NARRATIVES.values():
        assert narrative in text, narrative


def test_renders_actions_with_priority_labels(tmp_path):
    """우선순위는 코드가 정한 값이 한글 라벨로 표에 들어간다."""

    table_text = _table_text(_render(tmp_path))

    assert "신용정보법 제36조의2" in table_text
    assert "CODE_GENDER · DEMOGRAPHIC_PARITY" in table_text
    assert "GLOBAL_STABILITY" in table_text

    assert "높음" in table_text
    assert "중간" in table_text
    assert "낮음" in table_text


def test_priority_counts_match_actions(tmp_path):
    """집계표 값이 우선순위 산정 결과와 일치한다."""

    request = _request()
    counts = count_priorities(build_actions(request))
    table_text = _table_text(_render(tmp_path, request))

    assert "합계" in table_text
    assert str(counts["TOTAL"]) in table_text


def test_renders_findings_with_threshold_and_status(tmp_path):
    """지표는 관측값·임계값·상태가 함께 나와야 조치 판단이 가능하다."""

    table_text = _table_text(_render(tmp_path))

    assert "FAIL" in table_text
    assert "REVIEW" in table_text
    assert "WARNING" in table_text
    assert "0.45" in table_text
    assert "0.2" in table_text


def test_repeats_table_headers_and_prevents_row_split(tmp_path):
    document = _render(tmp_path)

    assert document.tables, "표가 하나도 없음"

    for table in document.tables:
        header_xml = table.rows[0]._tr.xml
        assert "tblHeader" in header_xml
        assert "cantSplit" in header_xml


def test_uses_a4_page_size(tmp_path):
    # twips 로 저장되며 되읽을 때 반올림 오차가 있어 허용범위로 본다.
    section = _render(tmp_path).sections[0]

    assert abs(section.page_width - Cm(21.0)) < 1000
    assert abs(section.page_height - Cm(29.7)) < 1000


def test_notes_each_area_when_no_action_needed(tmp_path):
    """조치 항목이 없어도 빈 절이 아니라 '없음' 표시가 나온다."""

    request = _request(
        compliance_gaps=[],
        self_check_gaps=[],
        fairness_findings=[],
        explainability_findings=[],
    )

    text = _paragraph_text(_render(tmp_path, request))

    assert "조치가 필요한 과제 없음" in text
    assert "규제 준수 영역에서 조치가 필요한 항목 없음" in text
    assert "임계값을 넘은 공정성 지표 없음" in text
    assert "임계값을 넘은 설명가능성 지표 없음" in text
