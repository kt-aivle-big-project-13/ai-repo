"""OpenAI 호환 LLM 호출 서비스.

보고서 생성 등 모든 LLM 사용을 이 한 곳으로 모아 같은 모델·엔드포인트를
쓰도록 통일한다. OpenAI Chat Completions 규격(`POST /chat/completions`)을
httpx 로 얇게 감싼다 — SDK 없이도 OpenAI 및 OpenAI 호환 엔드포인트를 함께
쓸 수 있다. 테스트 시에는 `client` 인자로 httpx.MockTransport 클라이언트를
주입해 실제 호출 없이 검증한다.
"""

from collections.abc import Iterable

import httpx

from app.core.config import Settings, get_settings
from app.schemas.llm import ChatMessage, LLMResult


class LLMConfigurationError(RuntimeError):
    """LLM 호출에 필요한 환경 설정(API 키 등)이 없는 경우."""


class LLMRequestError(RuntimeError):
    """LLM 호출·응답 처리에 실패한 경우."""


MessageInput = ChatMessage | dict[str, str]


def _to_payload_messages(messages: Iterable[MessageInput]) -> list[dict[str, str]]:
    """ChatMessage/dict 혼용 입력을 OpenAI 요청 형식으로 정규화한다."""
    payload_messages: list[dict[str, str]] = []
    for message in messages:
        if isinstance(message, ChatMessage):
            payload_messages.append({"role": message.role, "content": message.content})
        else:
            payload_messages.append(
                {"role": message["role"], "content": message["content"]}
            )
    if not payload_messages:
        raise LLMRequestError("messages 는 비어 있을 수 없습니다.")
    return payload_messages


def generate_chat_completion(
    messages: Iterable[MessageInput],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> LLMResult:
    """메시지 목록으로 채팅 완성을 요청하고 결과를 돌려준다.

    `model` 을 주지 않으면 설정의 통일 모델(`openai_model`)을 쓴다. API 키가
    없으면 `LLMConfigurationError`, 호출·응답 처리 실패는 `LLMRequestError`.
    """
    settings = settings or get_settings()

    if not settings.openai_api_key:
        raise LLMConfigurationError("OPENAI_API_KEY 가 설정되지 않았습니다.")

    payload: dict[str, object] = {
        "model": model or settings.openai_model,
        "messages": _to_payload_messages(messages),
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=settings.openai_timeout)

    try:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exception:
        raise LLMRequestError(
            f"LLM 호출이 상태코드 {exception.response.status_code} 로 실패했습니다: "
            f"{exception.response.text[:500]}"
        ) from exception
    except httpx.HTTPError as exception:
        raise LLMRequestError(f"LLM 호출 중 네트워크 오류: {exception}") from exception
    finally:
        if owns_client:
            client.close()

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exception:
        raise LLMRequestError(f"LLM 응답 형식이 올바르지 않습니다: {data}") from exception

    usage = data.get("usage") or {}
    return LLMResult(
        content=content,
        model=data.get("model") or str(payload["model"]),
        finish_reason=choice.get("finish_reason"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


def complete(
    prompt: str,
    *,
    system: str | None = None,
    **kwargs: object,
) -> str:
    """단순 프롬프트용 헬퍼 — 시스템/유저 메시지를 구성해 본문만 돌려준다."""
    messages: list[MessageInput] = []
    if system:
        messages.append(ChatMessage(role="system", content=system))
    messages.append(ChatMessage(role="user", content=prompt))
    return generate_chat_completion(messages, **kwargs).content  # type: ignore[arg-type]
