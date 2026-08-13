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

    # 대화형 질의 응답용 모델. 지정하지 않으면 통일 모델을 그대로 쓴다.
    # 리포트 생성과 달리 응답 지연이 UX 에 직결되므로, 필요할 때 .env 만 바꿔
    # 전환할 수 있도록 분리해 둔다.
    openai_chat_model: str | None = None

    # 동시 실행 제한 (`app.core.concurrency`). AI 서버는 워커 1개짜리 단일 프로세스라
    # 요청이 겹치는 만큼 같은 프로세스 안에서 메모리·CPU 를 나눠 쓴다.
    #
    # 분석 2 는 감사 한 건이 SHAP·공정성을 동시에 보내므로 한 감사를 대기 없이 처리하는
    # 크기다. 리포트 1 은 요청마다 Chromium 을 새로 띄우기 때문이다.
    analysis_concurrency: int = 2
    report_concurrency: int = 1

    # 실행 자리를 기다리는 상한. 넘으면 503 으로 돌려준다. 백엔드의 리포트 읽기
    # 타임아웃(600초)보다 짧게 둬, 백엔드가 이미 포기한 요청을 붙들고 있지 않게 한다.
    concurrency_acquire_timeout: float = 300.0

    # XGBoost 가 예측·SHAP 계산에 쓸 스레드 수. 지정하지 않으면 가용 코어를 전부
    # 잡으려 하고, 그런 요청이 동시에 여러 개면 코어 수를 크게 넘는 스레드가 서로
    # 경합해 오히려 느려진다.
    #
    # 기본값은 인스턴스 vCPU 와 같은 2다. 분석 동시 실행이 2라 최악의 경우 2코어에
    # 스레드 4개가 올라가지만, 요청이 하나뿐일 때 속도를 잃지 않는 쪽을 택했다.
    # 실측 후 조정 대상이다.
    xgboost_threads: int = 2

    @property
    def chat_model(self) -> str:
        """대화용 모델 ID. 미지정 시 통일 모델을 쓴다."""
        return self.openai_chat_model or self.openai_model


@lru_cache
def get_settings() -> Settings:
    """설정을 한 번만 로드해 캐시한다."""
    return Settings()
