"""개선 권고 가이드 섹션별 LLM 프롬프트.

판정은 LLM 이 하지 않는다. 조치가 필요한 항목은 백엔드에서 임계값 판정이 끝난 뒤
넘어오고, 우선순위도 코드가 정한다. LLM 은 각 항목에 대해 무엇을 어떻게 개선할지
서술하기만 한다.
"""

import json
from typing import Any

from app.schemas.improvement.improvement_guide import ImprovementGuideRequest

SYSTEM = (
    "너는 신용평가 AI 규제준수 감사의 개선 권고 가이드를 작성하는 도우미다. "
    "다음 규칙을 반드시 지킨다. "
    "(1) 제공된 데이터에 있는 값·사실만 사용하고, 없는 수치·법령·조항·근거는 지어내지 않는다. "
    "(2) 한국어 명사체(~함, ~음, ~임)로 쓰고 '~합니다'체를 쓰지 않는다. "
    "(3) 마크다운 제목·머리기호 없이 3~5문장의 서술 문단만 출력한다. "
    "(4) 조치가 필요한 항목과 그 우선순위는 이미 정해져 데이터로 주어진다. 판정을 새로 "
    "내리거나 우선순위를 바꾸지 않는다. "
    "(5) 각 항목에 대해 무엇을 어떻게 개선할지 실행 가능한 조치를 서술하되, 데이터에 없는 "
    "구체적 수치 목표(예: 특정 값까지 낮출 것)는 단정하지 않는다."
)

# 지표가 무엇을 재는지 — 개선 방향을 서술할 근거로 함께 전달한다.
METRIC_MEANINGS = {
    "DEMOGRAPHIC_PARITY": "집단 간 전체 승인율의 차이",
    "EQUAL_OPPORTUNITY": "정상(비연체) 고객의 집단 간 승인율 차이",
    "EQUALIZED_ODDS": "정상 고객 오거절률과 연체 고객 오승인률의 집단 간 차이 중 큰 값",
    "PROPORTIONAL_PARITY": "집단별 승인율의 최소/최대 비율 (80% Rule)",
    "FPR_PARITY": "실제 연체 고객을 승인한 비율의 집단 간 격차",
    "FDR_PARITY": "승인 고객 중 실제 연체 비율의 집단 간 격차",
    "FOR_PARITY": "거절 고객 중 실제 정상 비율의 집단 간 격차",
}


def _facts(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _findings(findings: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "보호속성": finding.attribute,
            "지표": finding.metric_code,
            "의미": METRIC_MEANINGS.get(finding.metric_code),
            "관측값": finding.value,
            "임계값": finding.threshold,
            "상태": finding.status,
        }
        for finding in findings
    ]


def overview_prompt(
    request: ImprovementGuideRequest,
    counts: dict[str, int],
) -> str:
    """1장 개선 개요."""

    facts = _facts(
        {
            "감사명": request.audit_name,
            "모델": request.model_name,
            "우선순위별_과제수": counts,
            "영역별_과제수": {
                "규제준수": len(request.compliance_gaps),
                "공정성": len(request.fairness_findings),
                "설명가능성": len(request.explainability_findings),
            },
        }
    )

    return (
        "아래는 이번 감사에서 조치가 필요한 것으로 확인된 과제의 집계다.\n\n"
        f"{facts}\n\n"
        "이 가이드가 무엇을 근거로 어떤 개선을 다루는지 서술하라. "
        "과제가 이미 임계값 판정을 거쳐 선별된 것이라는 점, 우선순위가 정해져 있다는 점을 "
        "밝혀라. 과제가 하나도 없으면 그 사실을 서술하라."
    )


def regulation_prompt(request: ImprovementGuideRequest) -> str:
    """3장 규제 준수 개선."""

    if not request.compliance_gaps and not request.self_check_gaps:
        return (
            "미준수로 판정된 법령 조항과 자가점검 미충족 항목이 모두 없다.\n\n"
            "규제 준수 영역에서 조치가 필요한 항목이 없다는 사실을 서술하라. "
            "다만 이 판단이 자가점검 응답에 기반한 것이라는 한계도 함께 밝혀라."
        )

    facts = _facts(
        {
            "미준수_조항": [
                {
                    "법령": gap.law_name,
                    "조항": gap.article_no,
                    "요지": gap.summary,
                    "근거": gap.evidence,
                }
                for gap in request.compliance_gaps
            ],
            "자가점검_미충족": [
                {"항목": gap.label} for gap in request.self_check_gaps
            ],
        }
    )

    return (
        "아래는 규제 준수 영역에서 조치가 필요한 항목이다.\n\n"
        f"{facts}\n\n"
        "각 항목에 대해 어떤 제도·절차를 마련하거나 보완해야 하는지 서술하라. "
        "법령이 요구하는 바와 현재 미충족 상태를 연결해 설명하라."
    )


def fairness_prompt(request: ImprovementGuideRequest) -> str:
    """4장 공정성 개선."""

    if not request.fairness_findings:
        return (
            "임계값을 넘은 공정성 지표가 없다.\n\n"
            "공정성 영역에서 조치가 필요한 지표가 없다는 사실을 서술하라. "
            "다만 지표가 기준 이내라는 것이 차별이 없음을 보장하지는 않는다는 점도 밝혀라."
        )

    facts = _facts({"조치필요_지표": _findings(request.fairness_findings)})

    return (
        "아래는 임계값을 넘어 조치가 필요한 공정성 지표다.\n\n"
        f"{facts}\n\n"
        "각 지표가 무엇을 재는지 쉬운 말로 풀고, 관측값이 임계값을 넘었다는 것이 어떤 "
        "상태를 뜻하는지 설명한 뒤, 데이터·피처·임계값 측면에서 검토할 개선 방향을 "
        "서술하라. 특정 집단에 대한 인과관계는 단정하지 않는다."
    )


def explainability_prompt(request: ImprovementGuideRequest) -> str:
    """5장 설명가능성 개선."""

    if not request.explainability_findings:
        return (
            "임계값을 넘은 설명가능성 지표가 없다.\n\n"
            "설명가능성 영역에서 조치가 필요한 지표가 없다는 사실을 서술하라."
        )

    facts = _facts({"조치필요_지표": _findings(request.explainability_findings)})

    return (
        "아래는 조치가 필요한 설명가능성 지표다.\n\n"
        f"{facts}\n\n"
        "각 지표가 무엇을 뜻하는지 설명하고, 설명 결과의 안정성·충실도를 높이기 위해 "
        "검토할 개선 방향을 서술하라."
    )


def recommendations_prompt(request: ImprovementGuideRequest) -> str:
    """7장 참고 권고(노력의무) — 위반이 아닌 권장 사항만 따로 서술."""

    if not request.self_check_recommendations:
        return (
            "노력의무 문항에서 '아니오'로 답한 항목이 없다.\n\n"
            "노력의무 영역에서 별도로 권고할 사항이 없다는 사실을 서술하라."
        )

    facts = _facts(
        {
            "노력의무_참고권고": [
                {"항목": gap.label} for gap in request.self_check_recommendations
            ]
        }
    )

    return (
        "아래는 노력의무 문항에서 '아니오'로 답한 항목이다.\n\n"
        f"{facts}\n\n"
        "이 항목들은 법이 '노력하여야 한다'고 정한 권장 사항이라 미이행이 위반은 아니라는 "
        "점을 먼저 밝혀라. 그런 다음 각 항목에 대해 어떤 조치를 하면 좋을지 권고 톤으로 "
        "서술하라. '위반', '미이행', '미충족'이라는 표현은 쓰지 않는다."
    )


def follow_up_prompt(
    request: ImprovementGuideRequest,
    counts: dict[str, int],
) -> str:
    """6장 이행 점검 항목."""

    facts = _facts(
        {
            "우선순위별_과제수": counts,
            "규제준수_과제": [
                f"{gap.law_name} {gap.article_no}"
                for gap in request.compliance_gaps
            ],
            "공정성_과제": [
                f"{finding.attribute} {finding.metric_code}"
                for finding in request.fairness_findings
            ],
            "설명가능성_과제": [
                finding.metric_code
                for finding in request.explainability_findings
            ],
        }
    )

    return (
        "아래는 이번 가이드에서 다룬 개선 과제 목록이다.\n\n"
        f"{facts}\n\n"
        "개선 조치 후 재감사에서 무엇을 확인해야 하는지 점검 관점을 서술하라. "
        "재감사 시점이나 구체적 목표 수치는 데이터에 없으므로 단정하지 않는다."
    )
