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
    "(3) 마크다운 제목·머리기호 없이 3~5문장의 서술 문단만 출력한다. "
    "(4) 단순 값 나열이 아니라 해석을 포함한다 — 각 지표가 무엇을 의미하는지 쉬운 말로 풀고, "
    "관측된 값으로 어느 집단이 상대적으로 유리·불리한지, 지표 간 패턴이 무엇을 시사하는지 서술한다. "
    "(5) 다만 판정 기준(임계값)이 없으므로 '공정함/차별 있음/충족/미흡' 같은 정책 판정과 "
    "인과관계 단정은 하지 않는다(관측된 연관·격차로만 해석)."
)

# 각 지표가 무엇을 재는지 — LLM 이 해석의 근거로 삼도록 프롬프트에 함께 전달한다.
METRIC_MEANINGS = {
    "demographic_parity_difference": "집단 간 전체 승인율의 최대 차이 (승인 기회 총량의 격차)",
    "equal_opportunity_difference": "정상(비연체) 고객의 집단 간 승인율 차이 (자격 있는 고객이 받는 기회의 격차)",
    "equalized_odds_difference": "정상 고객 오거절률과 연체 고객 오승인률의 집단 간 차이 중 큰 값",
    "proportional_parity_ratio": "집단별 승인율의 최소/최대 비율 (80% Rule 관례 기준 0.8 이상이면 격차가 작음)",
    "fpr_parity_difference": "실제 연체 고객을 승인한 비율(오승인, FPR)의 집단 간 격차",
    "fdr_parity_difference": "승인된 고객 중 실제 연체(오승인, FDR) 비율의 집단 간 격차",
    "for_parity_difference": "거절된 고객 중 실제 정상(오거절, FOR) 비율의 집단 간 격차",
}


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
        "집단 간 승인율·실제연체율을 해석하는 문단을 작성해라. 어느 집단의 승인율·연체율이 "
        "상대적으로 높고 낮은지 짚고, 승인율 격차가 실제연체율 차이로 설명되는 부분과 그렇지 "
        "않은 부분을 값에 근거해 해석해라(공정/차별·인과 단정은 금지)."
    )


def metric_results_prompt(audit: AuditRunResponse) -> str:
    facts = {
        "지표설명": METRIC_MEANINGS,
        "보호속성별지표": _attribute_metrics(audit),
        "집단별승인율": {
            attribute: {g.group: g.approval_rate for g in f.groups}
            for attribute, f in audit.fairness_by_attribute.items()
            if f.groups
        },
    }
    return (
        "다음은 공정성 지표의 값·의미와 집단별 승인율이다(difference 계열은 0에 가까울수록, "
        "proportional_parity_ratio 는 1에 가까울수록 격차가 작음).\n"
        f"{_facts(facts)}\n\n"
        "지표 결과를 해석하는 문단을 작성해라. 승인 기회 격차(DPD·Equal Opportunity·Proportional "
        "Parity)와 오류율 격차(Equalized Odds·FPR·FDR·FOR)가 각각 무엇을 의미하는지 풀고, 집단별 "
        "승인율을 근거로 어느 집단이 상대적으로 유리·불리한지, 격차가 큰 지표가 무엇을 시사하는지 "
        "해석해라. Proportional Parity 는 80% Rule(관례 기준 0.8) 대비 어느 수준인지 관측으로 언급하되, "
        "정책 판정(충족/미흡)이나 인과·차별 단정은 하지 마라."
    )


def tradeoff_prompt(audit: AuditRunResponse) -> str:
    facts = {
        "전체성능": {"auc": audit.performance.auc, "accuracy": audit.performance.accuracy},
        "집단별AUC": {
            attribute: {g.group: g.auc for g in f.groups if g.auc is not None}
            for attribute, f in audit.fairness_by_attribute.items()
            if any(g.auc is not None for g in f.groups)
        },
        "공정성지표": _attribute_metrics(audit),
    }
    return (
        "다음은 전체 모델 성능과 집단별 AUC다.\n"
        f"{_facts(facts)}\n\n"
        "전체 성능 대비 집단별 판별력(AUC) 차이를 해석하는 문단을 작성해라. 어느 집단에서 모델의 "
        "판별력이 상대적으로 낮은지, 그 판별력 차이가 앞의 공정성 지표 격차와 어떤 관련이 있어 보이는지 "
        "값에 근거해 해석해라(개선책·판정·인과 단정은 금지)."
    )


def overall_summary_prompt(audit: AuditRunResponse) -> str:
    return (
        "다음은 이번 편향진단의 지표 요약이다.\n"
        f"{_facts(_attribute_metrics(audit))}\n\n"
        "관측 결과를 종합해 해석 문단을 작성해라. 어느 보호속성·지표에서 격차가 두드러지는지, 그 격차가 "
        "'승인 기회'의 문제(DPD·Equal Opportunity)인지 '오류율'의 문제(FPR·FDR·FOR)인지 등 전체적인 "
        "편향 양상을 해석해라. 종합 판정·개선 권고·법령 언급은 하지 마라(별도 정책·후속 영역)."
    )
