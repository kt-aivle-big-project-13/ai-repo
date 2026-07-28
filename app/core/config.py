"""애플리케이션 환경 설정 (pydantic-settings).

`.env` 를 읽어 설정을 로드한다. 실제 값(API 키 등)은 저장소에 커밋하지 않는다.
LLM(OpenAI 호환) 연동에 필요한 값을 한곳에 모아, 보고서 생성 등 여러 곳에서
같은 모델·엔드포인트를 쓰도록 통일한다.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """`.env` 및 환경변수에서 읽는 애플리케이션 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # OpenAI 호환 LLM 설정. 보고서 생성 등 모든 LLM 호출이 이 값으로 통일된다.
    # openai_model 은 통일 대상 모델 ID 로 .env 에서 지정한다.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_timeout: float = 60.0

    # 법령 조문 요약 임베딩 생성용 모델. API 키·엔드포인트는 위 LLM 설정을 공유한다.
    openai_embedding_model: str = "text-embedding-3-small"


@lru_cache
def get_settings() -> Settings:
    """설정을 한 번만 로드해 캐시한다."""
    return Settings()
