"""설명가능성 리포트 섹션별 LLM 프롬프트 빌더.

숫자·판정은 코드가 삽입하고, LLM 은 서술 문단만 생성한다. 각 빌더는 해당
섹션에 필요한 근거 사실만 담아 환각을 줄인다.
"""

import json
from typing import Any

from app.schemas.shap import ShapReport

SYSTEM = (
    "너는 신용평가 AI 규제준수 설명가능성 감사 보고서를 작성하는 도우미다. "
    "다음 규칙을 반드시 지킨다. "
    "(1) 제공된 데이터에 있는 값·사실만 사용하고, 없는 수치·법령·근거는 지어내지 않는다. "
    "(2) 한국어 명사체(~함, ~음, ~임)로 쓰고 '~합니다'체를 쓰지 않는다. "
    "(3) 마크다운 제목·머리기호 없이 2~4문장의 서술 문단만 출력한다. "
    "(4) 수치를 새로 만들지 말고, 필요하면 제공된 수치를 그대로 인용한다."
)


def _facts(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def overview_purpose_prompt(report: ShapReport) -> str:
    facts = {
        "분석기법": "SHAP (TreeSHAP)",
        "모델_입력변수_수": report.schema_validation.model_feature_count,
        "분석_표본크기": report.sampling.get("audit_sample_size"),
        "데이터_행수": report.sampling.get("dataset_row_count"),
    }
    return (
        "다음은 이번 설명가능성 감사의 대상 정보다.\n"
        f"{_facts(facts)}\n\n"
        "이 감사의 목적과 범위를 서술하는 문단을 작성해라. "
        "설명가능성(SHAP) 검증이 대상이며, 편향·성능 평가는 범위 밖임을 밝혀라."
    )


def results_summary_prompt(report: ShapReport, overall_status: str) -> str:
    facts = {
        "종합상태": overall_status,
        "지표": [
            {"지표": m.metric, "값": m.value, "상태": m.status}
            for m in report.metrics
        ],
    }
    return (
        "다음은 설명가능성 감사의 핵심 결과다.\n"
        f"{_facts(facts)}\n\n"
        "감사 결과를 요약하는 문단을 작성해라. 어떤 지표가 충족·주의·미흡인지 "
        "제공된 상태값에 근거해 기술해라."
    )


def global_interpretation_prompt(report: ShapReport) -> str:
    facts = {
        "상위변수": [
            {
                "변수": f.feature,
                "영향방향": f.direction,
                "기여비율": f.contribution_ratio,
                "민감변수여부": f.is_sensitive,
            }
            for f in report.global_importance_top[:10]
        ],
    }
    return (
        "다음은 전역 SHAP 중요도 상위 변수다.\n"
        f"{_facts(facts)}\n\n"
        "모델의 주요 판단 경향을 서술하는 문단을 작성해라. 변수의 업무적 의미를 "
        "추측하지 말고, 제공된 영향방향(RISK_INCREASE/DECREASE)과 기여비율 중심으로만 "
        "기술해라."
    )


def reliability_summary_prompt(report: ShapReport) -> str:
    facts = {
        "지표": [
            {
                "지표": m.metric,
                "값": m.value,
                "기준": m.threshold,
                "상태": m.status,
                "부가": m.extra,
            }
            for m in report.metrics
        ],
    }
    return (
        "다음은 설명 신뢰성 검증 지표다.\n"
        f"{_facts(facts)}\n\n"
        "핵심 지표(안정성·충실성)와 보조 검증(가법성·순열 정합성)의 결과를 서술하는 "
        "문단을 작성해라. 값과 기준·상태에 근거해 충족·주의 여부를 기술해라."
    )


def overall_assessment_prompt(report: ShapReport, overall_status: str) -> str:
    facts = {
        "종합상태": overall_status,
        "지표별상태": {m.metric: m.status for m in report.metrics},
    }
    return (
        "다음은 설명가능성 감사의 종합 판정 근거다.\n"
        f"{_facts(facts)}\n\n"
        "종합 평가를 서술하는 문단을 작성해라. 종합상태의 근거가 되는 지표별 상태를 "
        "짚고, 주의·미흡 지표가 있으면 그 점을 명시해라. 새로운 권고나 법령은 언급하지 마라."
    )
