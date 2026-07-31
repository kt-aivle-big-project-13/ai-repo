"""고영향 AI 사전진단 보고서 생성 요청·응답 스키마."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

QUESTION_RULES = {
    "GATE_01": ("QUALITATIVE", "GATE", 0),
    "GATE_02": ("QUALITATIVE", "GATE", 0),
    "A_01": ("QUANTITATIVE", "A", 2),
    "A_02": ("QUANTITATIVE", "A", 2),
    "A_03": ("QUANTITATIVE", "A", 2),
    "B_01": ("QUANTITATIVE", "B", 1),
    "B_02": ("QUANTITATIVE", "B", 1),
    "B_03": ("QUANTITATIVE", "B", 1),
}

GATE_QUESTION_CODES = {"GATE_01", "GATE_02"}
QUANTITATIVE_QUESTION_CODES = {
    "A_01",
    "A_02",
    "A_03",
    "B_01",
    "B_02",
    "B_03",
}


class HighImpactReportAnswer(BaseModel):
    """사전진단 문항별 응답."""

    question_code: str = Field(min_length=1, max_length=30)
    question_text: str = Field(min_length=1)
    stage: Literal["QUALITATIVE", "QUANTITATIVE"]
    group: Literal["GATE", "A", "B"]
    answer: bool
    weight: int = Field(ge=0, le=2)
    score: int = Field(ge=0, le=2)


class HighImpactReportRequest(BaseModel):
    """백엔드에서 전달받는 고영향 AI 사전진단 보고서 데이터."""

    audit_id: int = Field(gt=0)
    assessment_id: int = Field(gt=0)
    audit_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_version: str | None = None
    assessed_at: datetime

    condition_met: bool
    group_a_score: int = Field(ge=0, le=6)
    group_b_score: int = Field(ge=0, le=3)
    total_score: int = Field(ge=0, le=9)
    result: Literal["HIGH_IMPACT", "NOT_APPLICABLE"]

    answers: list[HighImpactReportAnswer] = Field(
        min_length=2,
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_assessment_result(self):
        """문항 구성, 배점 및 최종 판정의 정합성을 검증한다."""

        answer_by_code = {
            answer.question_code: answer
            for answer in self.answers
        }

        if len(answer_by_code) != len(self.answers):
            raise ValueError("중복된 사전진단 문항이 포함되어 있습니다.")

        unknown_codes = set(answer_by_code) - set(QUESTION_RULES)

        if unknown_codes:
            raise ValueError(
                "지원하지 않는 사전진단 문항이 포함되어 있습니다: "
                + ", ".join(sorted(unknown_codes))
            )

        if not GATE_QUESTION_CODES.issubset(answer_by_code):
            raise ValueError(
                "정성 게이트 문항 GATE_01, GATE_02가 필요합니다."
            )

        for answer in self.answers:
            expected_stage, expected_group, expected_weight = (
                QUESTION_RULES[answer.question_code]
            )

            if answer.stage != expected_stage:
                raise ValueError(
                    f"{answer.question_code}의 진단 단계가 올바르지 않습니다."
                )

            if answer.group != expected_group:
                raise ValueError(
                    f"{answer.question_code}의 문항 그룹이 올바르지 않습니다."
                )

            if answer.weight != expected_weight:
                raise ValueError(
                    f"{answer.question_code}의 배점이 올바르지 않습니다."
                )

            expected_score = answer.weight if answer.answer else 0

            if answer.score != expected_score:
                raise ValueError(
                    f"{answer.question_code}의 응답과 점수가 일치하지 않습니다."
                )

        calculated_condition_met = any(
            answer_by_code[code].answer
            for code in GATE_QUESTION_CODES
        )

        if calculated_condition_met != self.condition_met:
            raise ValueError(
                "정성 게이트 결과가 문항 응답과 일치하지 않습니다."
            )

        submitted_quantitative_codes = (
            set(answer_by_code) & QUANTITATIVE_QUESTION_CODES
        )

        if self.condition_met:
            if submitted_quantitative_codes:
                raise ValueError(
                    "정성 게이트 충족 시 정량 문항을 포함할 수 없습니다."
                )
        elif (
            submitted_quantitative_codes
            != QUANTITATIVE_QUESTION_CODES
        ):
            raise ValueError(
                "정성 게이트 미충족 시 정량 문항 6개가 모두 필요합니다."
            )

        calculated_group_a_score = sum(
            answer.score
            for answer in self.answers
            if answer.group == "A"
        )
        calculated_group_b_score = sum(
            answer.score
            for answer in self.answers
            if answer.group == "B"
        )

        if calculated_group_a_score != self.group_a_score:
            raise ValueError(
                "A그룹 점수가 문항별 점수와 일치하지 않습니다."
            )

        if calculated_group_b_score != self.group_b_score:
            raise ValueError(
                "B그룹 점수가 문항별 점수와 일치하지 않습니다."
            )

        if self.group_a_score + self.group_b_score != self.total_score:
            raise ValueError(
                "합산 점수가 그룹별 점수와 일치하지 않습니다."
            )

        expected_result = (
            "HIGH_IMPACT"
            if self.condition_met or self.total_score >= 4
            else "NOT_APPLICABLE"
        )

        if self.result != expected_result:
            raise ValueError(
                "최종 판정이 사전진단 기준과 일치하지 않습니다."
            )

        return self


class HighImpactReportResponse(BaseModel):
    """생성된 고영향 AI 사전진단 PDF·Word 산출물 참조."""

    audit_id: int
    assessment_id: int
    pdf_report_s3_key: str
    word_report_s3_key: str
    generated_at: datetime
