"""LLM 호출 입출력 스키마."""

from typing import Literal

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """LLM 대화 메시지 한 건."""

    role: Role
    content: str


class LLMResult(BaseModel):
    """LLM 응답에서 뽑아낸 결과."""

    content: str
    model: str
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
