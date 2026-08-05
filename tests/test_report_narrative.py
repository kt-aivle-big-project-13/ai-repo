"""리포트 섹션 서술 반환 테스트.

핵심은 두 가지다. 목차 순서대로 제목과 함께 나오는가, 그리고 이 기능 때문에 LLM 호출이
늘지 않는가. 늘어난다면 "이미 만든 값을 담아 보낸다"는 전제가 깨진다.
"""

from pathlib import Path

import pytest

from app.schemas.report.report_narrative import build_narratives


def test_orders_by_title_map_not_dict_order():
    """목차 순서를 따르고, 서술 dict 의 삽입 순서에 좌우되지 않는다."""

    narratives = {
        "overall_summary": "종합",
        "overview_purpose": "개요",
        "metric_results": "지표",
    }
    titles = {
        "overview_purpose": "1. 감사 개요",
        "metric_results": "5. 공정성 지표 결과",
        "overall_summary": "7. 종합 (기술 요약)",
    }

    result = build_narratives(narratives, titles)

    assert [item.section_key for item in result] == [
        "overview_purpose",
        "metric_results",
        "overall_summary",
    ]
    assert result[0].title == "1. 감사 개요"
    assert result[0].content == "개요"


def test_skips_missing_or_empty_sections():
    """생성되지 않았거나 비어 있는 섹션은 담지 않는다."""

    result = build_narratives(
        {"overview": "내용", "empty": "", "missing_key_not_present": "x"},
        {"overview": "1. 개요", "empty": "2. 빈 절", "absent": "3. 없는 절"},
    )

    assert [item.section_key for item in result] == ["overview"]


@pytest.mark.parametrize(
    "module_name, titles_attr",
    [
        ("app.services.bias.bias_report", "NARRATIVE_TITLES"),
        ("app.services.compliance.compliance_report", "NARRATIVE_TITLES"),
        ("app.services.improvement.improvement_guide", "NARRATIVE_TITLES"),
        ("app.services.report.report", "NARRATIVE_TITLES"),
    ],
)
def test_every_generated_narrative_has_a_title(module_name, titles_attr):
    """서비스가 만드는 서술 키가 모두 목차 제목 매핑에 있어야 한다.

    키를 추가하고 제목을 빠뜨리면 그 섹션이 조용히 누락되므로 여기서 잡는다.
    """

    import importlib

    module = importlib.import_module(module_name)
    titles = getattr(module, titles_attr)

    source = Path(module.__file__).read_text(encoding="utf-8")
    body = source.split("def _build_narratives", maxsplit=1)[1]
    body = body.split("\ndef ", maxsplit=1)[0]

    generated_keys = {
        line.strip().split('"')[1]
        for line in body.splitlines()
        if line.strip().startswith('"') and "complete_fn(" in line
    }

    assert generated_keys, f"{module_name} 의 서술 키를 찾지 못함"
    assert generated_keys <= set(titles), (
        f"{module_name}: 제목 없는 서술 키 {generated_keys - set(titles)}"
    )


def test_bias_report_does_not_add_llm_calls(monkeypatch):
    """섹션 서술 반환 때문에 LLM 호출이 늘지 않는다."""

    import app.services.bias.bias_report as service
    from tests.test_bias_report import _audit, _request

    calls: list = []

    def fake_complete(prompt, system=None, **kwargs):
        calls.append(prompt)
        return "서술"

    monkeypatch.setattr(service, "analyze_fairness_s3", lambda *a, **k: _audit())
    monkeypatch.setattr(service, "complete", fake_complete)
    monkeypatch.setattr(
        service,
        "render_html_to_pdf",
        lambda html_path, pdf_path: Path(pdf_path).write_bytes(b"%PDF-t"),
    )
    monkeypatch.setattr(service, "upload_s3_object", lambda source, key: key)

    result = service.generate_bias_report(_request())

    # 섹션 5개 = 호출 5회. 서술을 응답에 담느라 다시 부르지 않는다.
    assert len(calls) == 5
    assert len(result.narratives) == 5
    assert [item.section_key for item in result.narratives] == list(
        service.NARRATIVE_TITLES
    )
