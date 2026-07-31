"""규제준수 판정서 섹션별 LLM 프롬프트.

판정 자체는 LLM 이 하지 않는다. 조항별 준수/미준수는 자가점검 응답에서 이미 정해져
넘어오고, LLM 은 그 결과를 서술로 풀어쓰기만 한다. 공정성 수치로 판정을 바꾸거나
새로 내리지 않도록 규칙에 못 박는다.
"""

import json
from typing import Any

from app.schemas.compliance_report import ComplianceReportRequest

SYSTEM = (
    "너는 신용평가 AI 규제준수 판정서를 작성하는 도우미다. "
    "다음 규칙을 반드시 지킨다. "
    "(1) 제공된 데이터에 있는 값·사실만 사용하고, 없는 수치·법령·조항·근거는 지어내지 않는다. "
    "(2) 한국어 명사체(~함, ~음, ~임)로 쓰고 '~합니다'체를 쓰지 않는다. "
    "(3) 마크다운 제목·머리기호 없이 3~5문장의 서술 문단만 출력한다. "
    "(4) 준수·미준수 판정은 이미 정해져 데이터로 주어진다. 판정을 새로 내리거나 뒤집지 않고, "
    "주어진 판정과 그 근거를 설명하기만 한다. "
    "(5) 판정 근거는 자가점검 응답과 그에 매핑된 법령 조항이다. 공정성 지표(승인율 격차 등)는 "
    "참고 맥락일 뿐이므로, 수치를 근거로 준수 여부를 판단하거나 단정하지 않는다."
)


def _facts(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _answers(request: ComplianceReportRequest) -> list[dict[str, Any]]:
    return [
        {"항목": answer.label, "응답": "예" if answer.answer else "아니오"}
        for answer in request.self_check_answers
    ]


def _mappings(request: ComplianceReportRequest) -> list[dict[str, Any]]:
    return [
        {
            "법령": mapping.law_name,
            "조항": mapping.article_no,
            "판정": mapping.compliance,
            "근거": mapping.evidence,
            "요지": mapping.summary,
        }
        for mapping in request.regulation_mappings
    ]


def _counts(request: ComplianceReportRequest) -> dict[str, int]:
    counts = {"COMPLIANT": 0, "NON_COMPLIANT": 0, "PENDING": 0}

    for mapping in request.regulation_mappings:
        counts[mapping.compliance] += 1

    return counts


def overview_prompt(request: ComplianceReportRequest) -> str:
    """1장 판정 개요 — 무엇을 무슨 근거로 판정했는지."""

    facts = _facts(
        {
            "감사명": request.audit_name,
            "모델": request.model_name,
            "자가점검_응답": _answers(request),
            "판정_대상_조항수": len(request.regulation_mappings),
        }
    )

    return (
        "아래는 규제준수 판정의 대상과 근거다.\n\n"
        f"{facts}\n\n"
        "이 판정서가 무엇을 대상으로 하고 어떤 근거로 판정했는지 서술하라. "
        "판정 근거가 운영기관의 자가점검 응답과 그에 매핑된 법령 조항이라는 점, "
        "모델의 공정성 수치로 준수 여부를 판정한 것이 아니라는 점을 분명히 밝혀라."
    )


def verdict_summary_prompt(request: ComplianceReportRequest) -> str:
    """2장 종합 판정 결과 — 집계와 전체 그림."""

    facts = _facts(
        {
            "집계": _counts(request),
            "조항별_판정": _mappings(request),
        }
    )

    return (
        "아래는 조항별 준수 판정 결과와 집계다.\n\n"
        f"{facts}\n\n"
        "전체 판정 결과를 요약하라. 준수·미준수 조항 수와, 미준수가 있다면 어떤 성격의 "
        "항목에서 발생했는지 설명하라. 주어진 판정을 바꾸지 말고 그대로 서술하라."
    )


def non_compliance_prompt(request: ComplianceReportRequest) -> str:
    """5장 미준수 항목 상세 — 무엇이 왜 미준수인지."""

    non_compliant = [
        mapping
        for mapping in request.regulation_mappings
        if mapping.compliance == "NON_COMPLIANT"
    ]

    if not non_compliant:
        facts = _facts({"미준수_조항": [], "자가점검_응답": _answers(request)})
        return (
            "아래 데이터에서 미준수로 판정된 조항이 없다.\n\n"
            f"{facts}\n\n"
            "미준수 조항이 없다는 사실과 그 근거가 된 자가점검 응답을 서술하라. "
            "다만 이 판정이 자가점검 응답에 기반한 것이라는 한계도 함께 밝혀라."
        )

    facts = _facts(
        {
            "미준수_조항": [
                {
                    "법령": mapping.law_name,
                    "조항": mapping.article_no,
                    "근거": mapping.evidence,
                    "요지": mapping.summary,
                }
                for mapping in non_compliant
            ]
        }
    )

    return (
        "아래는 미준수로 판정된 조항이다.\n\n"
        f"{facts}\n\n"
        "각 조항이 요구하는 바가 무엇이고 어떤 자가점검 응답 때문에 미준수로 판정됐는지 "
        "서술하라. 구체적인 개선 방안 제시는 이 판정서 범위 밖이므로 하지 않는다."
    )


def limitation_prompt(request: ComplianceReportRequest) -> str:
    """부록 판정 방법과 한계."""

    facts = _facts(
        {
            "판정_방식": "자가점검 응답 → 정적 매핑표 → 법령 조항별 준수 판정",
            "자가점검_항목수": len(request.self_check_answers),
            "판정_대상_조항수": len(request.regulation_mappings),
            "공정성_참고값_포함": request.audit_reference is not None,
        }
    )

    return (
        "아래는 이 판정서의 판정 방식이다.\n\n"
        f"{facts}\n\n"
        "이 판정이 어떤 방식으로 이뤄졌고 무엇을 보장하지 못하는지 서술하라. "
        "자가점검 응답은 운영기관이 스스로 답한 사실관계라 응답의 정확성에 의존한다는 점, "
        "공정성 지표에는 판정 임계값이 정해져 있지 않아 수치 기반 준수 판정은 이 판정서 "
        "범위 밖이라는 점을 포함하라."
    )
