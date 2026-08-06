"""편향진단 리포트 figure 생성 테스트.

리포트 생성 테스트(`test_bias_report.py`)는 HTML 에 그림이 들어갔는지까지만 본다.
여기서는 figure 자체의 규칙을 본다: 보호속성마다 몇 개를 그리는지, 데이터가 없을 때
무엇을 건너뛰는지, 그리고 한글 폰트 설정이 전역으로 새지 않는지.
"""

import base64

import matplotlib
import matplotlib.pyplot as plt

from app.schemas.audit import AuditRunResponse, FairnessMetricValues
from app.schemas.fairness.fairness import AttributeFairness, FairnessStatus, GroupStat
from app.schemas.performance import PerformanceSummary
from app.schemas.scoring import CalibrationSource, ThresholdInfo, ThresholdMethod
from app.services.bias.bias_figures import generate_bias_figures

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _groups(with_auc: bool = True) -> list[GroupStat]:
    return [
        GroupStat(group="M", n=3000, approval_rate=0.88, actual_default_rate=0.09,
                  tp=2600, fp=250, tn=100, fn=50, auc=0.74 if with_auc else None),
        GroupStat(group="F", n=2500, approval_rate=0.80, actual_default_rate=0.07,
                  tp=1900, fp=150, tn=120, fn=330, auc=0.77 if with_auc else None),
    ]


def _attribute(name: str = "CODE_GENDER", **overrides) -> AttributeFairness:
    values = {
        "attribute": name,
        "status": FairnessStatus.COMPUTED,
        "demographic_parity_difference": 0.08,
        "equal_opportunity_difference": 0.05,
        "equalized_odds_difference": 0.06,
        "proportional_parity_ratio": 0.9,
        "fpr_parity_difference": 0.04,
        "fdr_parity_difference": 0.03,
        "for_parity_difference": 0.02,
        "groups": _groups(),
        "excluded_groups": [],
        "note": None,
    }
    values.update(overrides)
    return AttributeFairness(**values)


def _audit(attributes: dict[str, AttributeFairness], auc: float | None = 0.755) -> AuditRunResponse:
    return AuditRunResponse(
        audit_id="42",
        audit_name="테스트 감사",
        threshold=ThresholdInfo(
            value=0.42, method=ThresholdMethod.TARGET_APPROVAL_RATE,
            basis="검증셋 90% 분위수", computed_from="validation_set",
        ),
        n_customers=5500,
        approval_rate=0.84,
        calibration_source=CalibrationSource.PLATFORM_COMPUTED,
        performance=PerformanceSummary(auc=auc, accuracy=0.71),
        fairness_by_attribute=attributes,
        fairness_summary={
            name: FairnessMetricValues(
                DEMOGRAPHIC_PARITY=0.08, EQUAL_OPPORTUNITY=0.05, EQUALIZED_ODDS=0.06,
                PROPORTIONAL_PARITY=0.9, FPR_PARITY=0.04, FDR_PARITY=0.03, FOR_PARITY=0.02,
            )
            for name in attributes
        },
        warnings=[],
    )


def _names(figures: list[dict[str, str]]) -> list[str]:
    return [figure["name"] for figure in figures]


def test_draws_three_figures_per_attribute():
    figures = generate_bias_figures(_audit({"CODE_GENDER": _attribute()}))

    assert _names(figures) == [
        "CODE_GENDER_groups",
        "CODE_GENDER_metrics",
        "CODE_GENDER_auc",
    ]

    # section 은 템플릿·Word 렌더러가 그림을 배치하는 키라 이름과 함께 고정이다.
    assert [figure["section"] for figure in figures] == ["groups", "metrics", "tradeoff"]
    assert all(figure["attribute"] == "CODE_GENDER" for figure in figures)
    assert all(figure["title"] for figure in figures)


def test_draws_figures_for_every_computed_attribute():
    """보호속성이 성별·연령대로 한정되지 않는다."""

    audit = _audit({
        "CODE_GENDER": _attribute(),
        "AGE_GROUP": _attribute("AGE_GROUP"),
        "OCCUPATION_TYPE": _attribute("OCCUPATION_TYPE"),
    })

    attributes = {figure["attribute"] for figure in generate_bias_figures(audit)}

    assert attributes == {"CODE_GENDER", "AGE_GROUP", "OCCUPATION_TYPE"}


def test_data_uri_decodes_to_png():
    figures = generate_bias_figures(_audit({"CODE_GENDER": _attribute()}))

    for figure in figures:
        prefix, encoded = figure["data_uri"].split(",", maxsplit=1)

        assert prefix == "data:image/png;base64"
        assert base64.b64decode(encoded, validate=True).startswith(PNG_MAGIC)


def test_skips_attribute_without_computed_status():
    """계산되지 않은 속성은 빈 차트를 그리지 않고 통째로 건너뛴다."""

    audit = _audit({
        "CODE_GENDER": _attribute(),
        "AGE_GROUP": _attribute(
            "AGE_GROUP",
            status=FairnessStatus.INSUFFICIENT_DATA,
            groups=[],
            note="집단이 하나뿐",
        ),
    })

    assert {figure["attribute"] for figure in generate_bias_figures(audit)} == {"CODE_GENDER"}


def test_skips_attribute_without_groups():
    """상태가 COMPUTED 여도 집단 통계가 없으면 그릴 게 없다."""

    audit = _audit({"CODE_GENDER": _attribute(groups=[])})

    assert generate_bias_figures(audit) == []


def test_omits_metric_figure_without_difference_metrics():
    """격차 지표가 전부 없으면 지표 차트를 생략한다."""

    audit = _audit({"CODE_GENDER": _attribute(
        demographic_parity_difference=None,
        equal_opportunity_difference=None,
        equalized_odds_difference=None,
        fpr_parity_difference=None,
        fdr_parity_difference=None,
        for_parity_difference=None,
    )})

    names = _names(generate_bias_figures(audit))

    assert "CODE_GENDER_metrics" not in names
    # 나머지 두 장은 그대로 나와야 한다.
    assert names == ["CODE_GENDER_groups", "CODE_GENDER_auc"]


def test_omits_auc_figure_without_group_auc():
    """집단별 AUC 가 없으면 트레이드오프 차트를 생략한다."""

    audit = _audit({"CODE_GENDER": _attribute(groups=_groups(with_auc=False))})

    names = _names(generate_bias_figures(audit))

    assert "CODE_GENDER_auc" not in names
    assert names == ["CODE_GENDER_groups", "CODE_GENDER_metrics"]


def test_draws_auc_figure_without_overall_auc():
    """전체 AUC 가 없으면 기준선만 빠지고 집단별 차트는 그대로 그린다."""

    audit = _audit({"CODE_GENDER": _attribute()}, auc=None)

    assert "CODE_GENDER_auc" in _names(generate_bias_figures(audit))


def test_does_not_leak_font_settings_to_global_rcparams():
    """한글 폰트 설정은 rc_context 안에서만 적용돼야 한다.

    전역 rcParams 를 바꾸면 같은 프로세스에서 그리는 다른 figure(SHAP 등)와
    테스트 실행 순서까지 영향을 받는다.

    기준값은 반드시 기본값에서 새로 잡는다 — 앞선 테스트가 이미 전역을 오염시킨
    상태에서 기준을 찍으면 "바뀐 값끼리 같다"가 되어 누출을 못 잡는다.
    """

    saved = plt.rcParams.copy()

    try:
        matplotlib.rcdefaults()
        before = (plt.rcParams["font.family"], plt.rcParams["axes.unicode_minus"])

        generate_bias_figures(_audit({"CODE_GENDER": _attribute("성별")}))

        assert (plt.rcParams["font.family"], plt.rcParams["axes.unicode_minus"]) == before
    finally:
        plt.rcParams.update(saved)


def test_closes_figures():
    """figure 를 닫지 않으면 리포트를 여러 번 만들 때 메모리에 쌓인다."""

    plt.close("all")

    generate_bias_figures(_audit({"CODE_GENDER": _attribute()}))

    assert plt.get_fignums() == []
