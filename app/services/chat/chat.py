"""감사 질의 응답 서비스.

계산하지도 판정하지도 않는다. 백엔드가 조립해 보낸 근거만으로 답하고, 답변이 실제로
인용한 근거만 응답에 담는다.

`grounding_status` 는 코드가 판정한다. LLM 이 스스로 "근거 있음"이라고 말하는 것을
믿지 않고, 인용 표기와 수치 대조로 확인한다.
"""

import re
from datetime import datetime, timezone
from typing import Callable

from app.core.config import get_settings
from app.schemas.chat.chat import (
    ChatAnswerRequest,
    ChatAnswerResponse,
    Citation,
    GroundingStatus,
)
from app.services.chat import chat_prompts
from app.services.chat.chat_prompts import (
    NO_GROUNDING_ANSWER,
    SYSTEM,
    GroundingItem,
)
from app.services.llm import complete

CITATION_PATTERN = re.compile(r"\[(\d+)\]")

# 문면에서 [n] 표기를 지울 때 쓴다. 인용 파싱(CITATION_PATTERN)과 별개로, 연속된
# [1][3] 같은 표기와 그 앞의 공백까지 한 번에 지워 지운 자리에 빈 공백이 남지 않게 한다.
# \s 대신 [ \t] 만 지우는 이유: \s는 개행도 포함해서, 줄 시작에 표기가 오면
# ("문장\n[1]다음 문장") 그 앞의 줄바꿈까지 지워 문장이 한 줄로 붙어버린다.
CITATION_MARKER_PATTERN = re.compile(r"[ \t]*(?:\[\d+\])+")

# 지표 값처럼 소수점이 있는 수만 검증한다. 정수는 "1장", "5개", 연도처럼 근거와
# 무관하게 등장하는 경우가 많아 오탐이 커진다.
DECIMAL_PATTERN = re.compile(r"\d+\.\d+")


def _parse_citations(
    answer: str,
    items: list[GroundingItem],
) -> list[Citation]:
    """답변에 표기된 [n] 을 실제 인용으로 바꾼다. 없는 번호는 무시한다."""

    by_index = {item.index: item for item in items}
    citations: list[Citation] = []
    seen: set[int] = set()

    for raw in CITATION_PATTERN.findall(answer):
        index = int(raw)

        if index in seen or index not in by_index:
            continue

        seen.add(index)
        citations.append(by_index[index].citation)

    return citations


def _strip_citation_markers(answer: str) -> str:
    """답변 문면에서 [n] 표기를 지운다.

    번호는 근거 목록의 순번일 뿐 사용자에게는 의미가 없고, 실제 인용 내용은
    이미 `citations`로 구조화해 돌려주므로 문장에는 남기지 않는다.
    """

    return CITATION_MARKER_PATTERN.sub("", answer)


def _has_unsupported_number(
    answer: str,
    question: str,
    items: list[GroundingItem],
) -> bool:
    """근거에 없는 소수 값이 답변에 있으면 환각으로 본다.

    질문에 나온 수는 대조 대상에 포함한다. "임계값을 0.5 로 바꾸면?" 처럼 사용자가
    직접 쓴 값을 답변이 되받는 것은 지어낸 것이 아니기 때문이다.
    """

    known_text = " ".join(
        [question, *(item.searchable for item in items)]
    )

    return any(
        number not in known_text
        for number in DECIMAL_PATTERN.findall(answer)
    )


def _judge_grounding(
    answer: str,
    question: str,
    citations: list[Citation],
    items: list[GroundingItem],
) -> GroundingStatus:
    if not citations:
        return "PARTIAL"

    if _has_unsupported_number(answer, question, items):
        return "PARTIAL"

    return "GROUNDED"


def generate_chat_answer(
    request: ChatAnswerRequest,
    complete_fn: Callable[..., str] = complete,
) -> ChatAnswerResponse:
    """주입된 근거로 질문에 답한다."""

    generated_at = datetime.now(timezone.utc).isoformat()
    items = chat_prompts.build_grounding_items(request)

    # 근거가 하나도 없으면 LLM 을 호출할 이유가 없다. 호출해봐야 지어내거나
    # 거부할 뿐이라, 비용을 쓰지 않고 결정적으로 거부한다.
    if not items:
        return ChatAnswerResponse(
            audit_id=request.audit_id,
            answer=NO_GROUNDING_ANSWER,
            citations=[],
            grounding_status="NOT_GROUNDED",
            generated_at=generated_at,
        )

    answer = complete_fn(
        chat_prompts.build_prompt(request, items),
        system=SYSTEM,
        model=get_settings().chat_model,
    ).strip()

    citations = _parse_citations(answer, items)
    grounding_status = _judge_grounding(
        answer,
        request.question,
        citations,
        items,
    )

    return ChatAnswerResponse(
        audit_id=request.audit_id,
        answer=_strip_citation_markers(answer),
        citations=citations,
        grounding_status=grounding_status,
        generated_at=generated_at,
    )