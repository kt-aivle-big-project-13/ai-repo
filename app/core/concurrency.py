"""무거운 요청의 동시 실행 제한.

AI 서버는 uvicorn 워커 1개짜리 단일 프로세스다. FastAPI 가 sync 라우터를 스레드풀
에서 돌리므로 요청은 얼마든지 겹칠 수 있는데, 겹치는 만큼 같은 프로세스 안에서
메모리와 CPU 를 나눠 쓴다. 감사 2건이 동시에 들어왔을 때 분석·리포트 요청이 한꺼번에
물려 프로세스가 메모리 고갈로 강제 종료된 적이 있다.

받아들이는 개수를 제한해 초과분을 대기시킨다. 거절하지 않고 기다리게 하는 이유는,
버스트가 지나가면 처리할 수 있는 요청을 미리 실패시킬 이유가 없기 때문이다. 다만
무한정 기다리면 스레드만 쌓이므로 상한을 두고, 넘으면 503 으로 돌려준다.

제한값은 `.env` 로 조정한다 (`app.core.config.Settings`).
"""

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ConcurrencyLimitExceeded(RuntimeError):
    """대기 상한 안에 실행 자리를 얻지 못한 경우."""


class Limiter:
    """이름 붙은 동시 실행 제한.

    `threading.Semaphore` 를 쓰는 이유는 제한 대상이 전부 sync 라우터이기 때문이다
    (FastAPI 가 스레드풀에서 실행한다). asyncio 세마포어는 여기서 동작하지 않는다.
    """

    def __init__(self, name: str, permits: int, timeout: float) -> None:
        if permits < 1:
            raise ValueError(f"{name} 동시 실행 제한은 1 이상이어야 합니다: {permits}")

        self.name = name
        self.permits = permits
        self.timeout = timeout
        self._semaphore = threading.BoundedSemaphore(permits)

    @contextmanager
    def slot(self) -> Iterator[None]:
        """실행 자리를 하나 잡는다. 상한까지 못 잡으면 예외를 올린다."""

        if not self._semaphore.acquire(timeout=self.timeout):
            raise ConcurrencyLimitExceeded(
                f"{self.name} 동시 실행 한도({self.permits})가 "
                f"{self.timeout:.0f}초 동안 해소되지 않았습니다."
            )

        try:
            yield
        finally:
            self._semaphore.release()


_settings = get_settings()

# 분석(SHAP·공정성)과 리포트를 따로 제한한다. 한 풀로 묶으면 수십 초짜리 리포트가
# 자리를 잡고 있는 동안 분석이 밀린다 — 분석은 감사의 본 작업이고 리포트는 부수
# 작업이라, 리포트 때문에 분석이 대기하는 상황을 만들지 않는다.
analysis_limiter = Limiter(
    "분석",
    _settings.analysis_concurrency,
    _settings.concurrency_acquire_timeout,
)

# 리포트는 LLM 호출·figure 생성에 더해 요청마다 Chromium 을 새로 띄운다
# (`app/services/report/report_pdf.py`). 기본값 1 이 곧 "Chromium 은 한 번에 하나"라,
# PDF 렌더용 제한을 따로 두지 않는다.
report_limiter = Limiter(
    "리포트",
    _settings.report_concurrency,
    _settings.concurrency_acquire_timeout,
)


@contextmanager
def _slot_or_503(limiter: Limiter) -> Iterator[None]:
    """실행 자리를 잡되, 상한을 넘기면 503 으로 바꿔 올린다.

    503 은 "지금은 못 받지만 서버가 고장난 건 아니다"라는 뜻이라, 백엔드가 재시도
    대상으로 구분할 수 있다. 500 으로 나가면 입력이 잘못된 경우와 섞인다.
    """

    try:
        with limiter.slot():
            yield
    except ConcurrencyLimitExceeded as exception:
        logger.warning("동시 실행 한도 초과로 요청을 거절합니다: %s", exception)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exception),
        ) from exception


def analysis_slot() -> Iterator[None]:
    """분석 엔드포인트용 FastAPI 의존성."""

    with _slot_or_503(analysis_limiter):
        yield


def report_slot() -> Iterator[None]:
    """리포트 엔드포인트용 FastAPI 의존성."""

    with _slot_or_503(report_limiter):
        yield
