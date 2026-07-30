"""편향진단 리포트 섹션별 LLM 프롬프트 빌더.

수치·표·그림은 코드가 삽입하고 LLM 은 서술 문단만 생성한다. 이번 리포트는
임계값 기반 판정이 없으므로, 프롬프트에서 '충족/미흡/차별' 단정을 금지하고
값·격차 크기만 기술하게 한다.
"""

import json
from typing import Any

from app.schemas.audit import AuditRunResponse

SYSTEM = (
    "너는 신용평가 AI 규제준수 편향진단 감사 보고서를 작성하는 도우미다. "
    "다음 규칙을 반드시 지킨다. "
    "(1) 제공된 데이터에 있는 값·사실만 사용하고, 없는 수치·법령·근거는 지어내지 않는다. "
    "(2) 한국어 명사체(~함, ~음, ~임)로 쓰고 '~합니다'체를 쓰지 않는다. "
    "(3) 마크다운 제목·머리기호 없이 2~4문장의 서술 문단만 출력한다. "
    "(4) 이 리포트는 판정 기준(임계값)이 없으므로 '공정함/차별 있음/충족/미흡' 같은 "
    "단정을 하지 말고, 관측된 값과 집단 간 격차의 크기만 기술한다."
)


def _facts(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _attribute_metrics(audit: AuditRunResponse) -> dict[str, Any]:
    return {
        attribute: {
            "demographic_parity_difference": f.demographic_parity_difference,
            "equal_opportunity_difference": f.equal_opportunity_difference,
            "equalized_odds_difference": f.equalized_odds_difference,
            "proportional_parity_ratio": f.proportional_parity_ratio,
            "fpr_parity_difference": f.fpr_parity_difference,
            "fdr_parity_difference": f.fdr_parity_difference,
            "for_parity_difference": f.for_parity_difference,
            "status": f.status.value,
        }
        for attribute, f in audit.fairness_by_attribute.items()
    }


def overview_purpose_prompt(audit: AuditRunResponse) -> str:
    facts = {
        "고객수": audit.n_customers,
        "전체승인율": audit.approval_rate,
        "승인임계값": audit.threshold.value,
        "보호속성": list(audit.fairness_by_attribute.keys()),
    }
    return (
        "다음은 이번 편향진단 감사의 대상 정보다.\n"
        f"{_facts(facts)}\n\n"
        "이 감사의 목적과 범위를 서술하는 문단을 작성해라. Fairlearn 기반 집단 간 "
        "공정성 지표 진단이 대상이며, 설명가능성(SHAP)은 범위 밖임을 밝혀라."
    )


def group_results_prompt(audit: AuditRunResponse) -> str:
    facts = {
        attribute: [
            {
                "집단": g.group,
                "인원": g.n,
                "승인율": g.approval_rate,
                "실제연체율": g.actual_default_rate,
            }
            for g in f.groups
        ]
        for attribute, f in audit.fairness_by_attribute.items()
        if f.groups
    }
    return (
        "다음은 보호속성별 집단 통계다.\n"
        f"{_facts(facts)}\n\n"
        "집단 간 승인율과 실제연체율의 차이 경향을 서술하는 문단을 작성해라. "
        "어느 집단이 상대적으로 높고 낮은지 값에 근거해 기술하되, 공정/차별을 단정하지 마라."
    )


def metric_results_prompt(audit: AuditRunResponse) -> str:
    return (
        "다음은 보호속성별 공정성 지표 값이다(difference 계열은 0에 가까울수록, "
        "proportional_parity_ratio 는 1에 가까울수록 격차가 작음).\n"
        f"{_facts(_attribute_metrics(audit))}\n\n"
        "지표 결과를 서술하는 문단을 작성해라. 격차가 상대적으로 큰 지표·보호속성을 "
        "값에 근거해 지목하되, 판정(충족/미흡)은 하지 마라."
    )


def tradeoff_prompt(audit: AuditRunResponse) -> str:
    facts = {
        "전체성능": {"auc": audit.performance.auc, "accuracy": audit.performance.accuracy},
        "집단별AUC": {
            attribute: {g.group: g.auc for g in f.groups if g.auc is not None}
            for attribute, f in audit.fairness_by_attribute.items()
            if any(g.auc is not None for g in f.groups)
        },
    }
    return (
        "다음은 전체 모델 성능과 집단별 AUC다.\n"
        f"{_facts(facts)}\n\n"
        "전체 성능 대비 집단별 판별력(AUC) 차이를 서술하는 문단을 작성해라. "
        "성능과 공정성 격차의 관계를 값에 근거해 기술하되, 개선책이나 판정은 넣지 마라."
    )


def overall_summary_prompt(audit: AuditRunResponse) -> str:
    return (
        "다음은 이번 편향진단의 지표 요약이다.\n"
        f"{_facts(_attribute_metrics(audit))}\n\n"
        "관측 결과를 종합해 기술 요약 문단을 작성해라. 격차가 두드러진 지점을 값으로 "
        "짚되, 종합 판정·개선 권고·법령 언급은 하지 마라(별도 정책·후속 영역)."
    )
