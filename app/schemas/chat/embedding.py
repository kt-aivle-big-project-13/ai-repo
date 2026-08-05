"""법령 조문 요약 임베딩 생성 요청·응답 스키마.

필드명은 백엔드 `EmbeddingRequest`/`EmbeddingResponse`(fin-audit-ai
`global/ai/dto`)와 1:1로 맞춘다. 백엔드는 조문을 하나씩 순회하며 호출하므로
배치가 아닌 단건(`text`) 요청이다.
"""

from pydantic import BaseModel, Field


class EmbeddingGenerateRequest(BaseModel):
    """백엔드에서 전달받는 임베딩 생성 요청 (단건)."""

    text: str = Field(min_length=1)


class EmbeddingGenerateResponse(BaseModel):
    """백엔드 EmbeddingResponse(float[] embedding, String model)와 동일한 응답."""

    embedding: list[float]
    model: str
