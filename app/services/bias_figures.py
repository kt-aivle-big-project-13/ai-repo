"""편향진단 리포트용 figure 생성.

공정성 감사 응답(`AuditRunResponse`)의 집단 통계·지표로 matplotlib 차트를 그려
base64 data URI 로 돌려준다(자기완결 HTML 임베드용). 이미지 안 텍스트는 한글
폰트 부재로 깨질 수 있어 영문으로 두고, 한글 캡션은 템플릿(figcaption)이 단다.
"""

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.schemas.audit import AuditRunResponse
from app.schemas.fairness import FairnessStatus

# 이미지 내부 라벨(영문) → 캡션(한글)은 dict["title"] 로 분리
_DIFF_METRICS = [
    ("DPD", "demographic_parity_difference"),
    ("EqualOpp", "equal_opportunity_difference"),
    ("EqOdds", "equalized_odds_difference"),
    ("FPR", "fpr_parity_difference"),
    ("FDR", "fdr_parity_difference"),
    ("FOR", "for_parity_difference"),
]


def _to_data_uri(fig) -> str:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def generate_bias_figures(audit: AuditRunResponse) -> list[dict[str, str]]:
    """보호속성별로 차트를 그려 목록으로 돌려준다.

    각 항목: {name, attribute, section, title(캡션), data_uri}. section 은
    groups/metrics/tradeoff 로, 템플릿이 해당 장에 배치한다.
    """
    figures: list[dict[str, str]] = []

    for attribute, fairness in audit.fairness_by_attribute.items():
        if fairness.status != FairnessStatus.COMPUTED or not fairness.groups:
            continue

        groups = fairness.groups
        labels = [g.group for g in groups]
        positions = range(len(labels))

        # 1) 집단별 승인율·실제연체율
        fig, ax = plt.subplots(figsize=(6.2, 3.4))
        width = 0.38
        ax.bar(
            [i - width / 2 for i in positions],
            [g.approval_rate for g in groups],
            width, label="Approval rate", color="#2f66e9",
        )
        ax.bar(
            [i + width / 2 for i in positions],
            [g.actual_default_rate for g in groups],
            width, label="Default rate", color="#e9a72f",
        )
        ax.set_xticks(list(positions))
        ax.set_xticklabels(labels, rotation=0)
        ax.set_ylim(0, 1)
        ax.set_ylabel("Rate")
        ax.set_title(f"Approval / default rate by group ({attribute})")
        ax.legend(fontsize=8)
        figures.append({
            "name": f"{attribute}_groups",
            "attribute": attribute,
            "section": "groups",
            "title": f"{attribute} 집단별 승인율·실제연체율",
            "data_uri": _to_data_uri(fig),
        })

        # 2) 공정성 지표 격차 (difference 계열, 0에 가까울수록 공정)
        items = [
            (label, getattr(fairness, field))
            for label, field in _DIFF_METRICS
            if getattr(fairness, field) is not None
        ]
        if items:
            fig, ax = plt.subplots(figsize=(6.2, 3.2))
            ax.bar([k for k, _ in items], [v for _, v in items], color="#c0504d")
            ax.set_ylim(bottom=0)
            ax.set_ylabel("Gap (0 = fair)")
            ax.set_title(f"Fairness metric gaps ({attribute})")
            figures.append({
                "name": f"{attribute}_metrics",
                "attribute": attribute,
                "section": "metrics",
                "title": f"{attribute} 공정성 지표 격차 (0에 가까울수록 공정)",
                "data_uri": _to_data_uri(fig),
            })

        # 3) 집단별 AUC (성능-공정성 트레이드오프)
        aucs = [(g.group, g.auc) for g in groups if g.auc is not None]
        if aucs:
            fig, ax = plt.subplots(figsize=(6.2, 3.2))
            ax.bar([g for g, _ in aucs], [a for _, a in aucs], color="#4caf68")
            if audit.performance.auc is not None:
                ax.axhline(
                    audit.performance.auc, color="#333", linestyle="--",
                    label=f"Overall AUC {audit.performance.auc:.3f}",
                )
                ax.legend(fontsize=8)
            ax.set_ylim(0, 1)
            ax.set_ylabel("AUC")
            ax.set_title(f"AUC by group ({attribute})")
            figures.append({
                "name": f"{attribute}_auc",
                "attribute": attribute,
                "section": "tradeoff",
                "title": f"{attribute} 집단별 AUC (점선=전체 AUC)",
                "data_uri": _to_data_uri(fig),
            })

    return figures
