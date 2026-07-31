"""고영향 AI 사전진단 보고서 스키마 테스트."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.high_impact_report import HighImpactReportRequest


def _gate_answer(
    question_code: str,
    answer: bool,
) -> dict:
    return {
        "question_code": question_code,
        "question_text": f"{question_code} 질문",
        "stage": "QUALITATIVE",
        "group": "GATE",
        "answer": answer,
        "weight": 0,
        "score": 0,
    }


def _quantitative_answer(
    question_code: str,
    group: str,
    answer: bool,
) -> dict:
    weight = 2 if group == "A" else 1

    return {
        "question_code": question_code,
        "question_text": f"{question_code} 질문",
        "stage": "QUANTITATIVE",
        "group": group,
        "answer": answer,
        "weight": weight,
        "score": weight if answer else 0,
    }


def _qualitative_payload() -> dict:
    return {
        "audit_id": 8,
        "assessment_id": 10,
        "audit_name": "신용평가 모델 감사",
        "model_name": "신용평가 모델",
        "model_version": "1.0",
        "assessed_at": "2026-07-31T10:00:00+09:00",
        "condition_met": True,
        "group_a_score": 0,
        "group_b_score": 0,
        "total_score": 0,
        "result": "HIGH_IMPACT",
        "answers": [
            _gate_answer("GATE_01", True),
            _gate_answer("GATE_02", False),
        ],
    }


def _quantitative_payload() -> dict:
    return {
        "audit_id": 8,
        "assessment_id": 11,
        "audit_name": "신용평가 모델 감사",
        "model_name": "신용평가 모델",
        "model_version": "1.0",
        "assessed_at": "2026-07-31T10:00:00+09:00",
        "condition_met": False,
        "group_a_score": 4,
        "group_b_score": 0,
        "total_score": 4,
        "result": "HIGH_IMPACT",
        "answers": [
            _gate_answer("GATE_01", False),
            _gate_answer("GATE_02", False),
            _quantitative_answer("A_01", "A", True),
            _quantitative_answer("A_02", "A", True),
            _quantitative_answer("A_03", "A", False),
            _quantitative_answer("B_01", "B", False),
            _quantitative_answer("B_02", "B", False),
            _quantitative_answer("B_03", "B", False),
        ],
    }


def test_accepts_qualitative_high_impact_result() -> None:
    request = HighImpactReportRequest(**_qualitative_payload())

    assert request.result == "HIGH_IMPACT"
    assert request.condition_met is True
    assert len(request.answers) == 2


def test_accepts_quantitative_high_impact_result() -> None:
    request = HighImpactReportRequest(**_quantitative_payload())

    assert request.result == "HIGH_IMPACT"
    assert request.total_score == 4
    assert len(request.answers) == 8


def test_rejects_answer_score_mismatch() -> None:
    payload = deepcopy(_quantitative_payload())
    payload["answers"][2]["score"] = 0

    with pytest.raises(
        ValidationError,
        match="응답과 점수가 일치하지 않습니다",
    ):
        HighImpactReportRequest(**payload)


def test_rejects_missing_quantitative_answers() -> None:
    payload = deepcopy(_quantitative_payload())
    payload["answers"] = payload["answers"][:-1]

    with pytest.raises(
        ValidationError,
        match="정량 문항 6개가 모두 필요합니다",
    ):
        HighImpactReportRequest(**payload)


def test_rejects_inconsistent_final_result() -> None:
    payload = deepcopy(_quantitative_payload())
    payload["result"] = "NOT_APPLICABLE"

    with pytest.raises(
        ValidationError,
        match="최종 판정이 사전진단 기준과 일치하지 않습니다",
    ):
        HighImpactReportRequest(**payload)