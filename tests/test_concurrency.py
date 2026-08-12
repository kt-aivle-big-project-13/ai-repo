"""동시 실행 제한 테스트.

제한이 실제로 겹침을 막는지, 그리고 상한을 넘겼을 때 백엔드가 구분할 수 있는
상태코드로 나가는지를 본다.
"""

import threading
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.concurrency import (
    ConcurrencyLimitExceeded,
    Limiter,
    _slot_or_503,
)


def test_limiter_rejects_invalid_permits():
    with pytest.raises(ValueError):
        Limiter("테스트", 0, 1.0)


def test_limiter_allows_up_to_permits_concurrently():
    limiter = Limiter("테스트", 2, timeout=5.0)
    running = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def work():
        nonlocal running, peak
        with limiter.slot():
            with lock:
                running += 1
                peak = max(peak, running)
            release.wait(timeout=5.0)
            with lock:
                running -= 1

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()

    # 앞의 2건이 자리를 잡을 때까지 기다린다.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with lock:
            if running == 2:
                break
        time.sleep(0.01)

    with lock:
        assert running == 2, "허용치를 넘겨 동시에 실행되면 안 된다"

    release.set()
    for thread in threads:
        thread.join(timeout=5.0)

    assert peak == 2


def test_limiter_raises_when_wait_exceeds_timeout():
    limiter = Limiter("테스트", 1, timeout=0.05)
    release = threading.Event()
    holder_ready = threading.Event()

    def hold():
        with limiter.slot():
            holder_ready.set()
            release.wait(timeout=5.0)

    thread = threading.Thread(target=hold)
    thread.start()
    holder_ready.wait(timeout=5.0)

    try:
        with pytest.raises(ConcurrencyLimitExceeded):
            with limiter.slot():
                pass
    finally:
        release.set()
        thread.join(timeout=5.0)


def test_limiter_releases_slot_when_body_raises():
    limiter = Limiter("테스트", 1, timeout=0.5)

    with pytest.raises(RuntimeError):
        with limiter.slot():
            raise RuntimeError("작업 실패")

    # 앞 요청이 실패했다고 자리가 잠기면 안 된다.
    with limiter.slot():
        pass


def test_limit_exceeded_maps_to_503():
    """백엔드가 '지금은 못 받음'을 서버 오류와 구분할 수 있어야 한다."""

    limiter = Limiter("테스트", 1, timeout=0.05)

    def slot():
        with _slot_or_503(limiter):
            yield

    app = FastAPI()

    @app.get("/work", dependencies=[Depends(slot)])
    def work():
        time.sleep(0.3)
        return {"ok": True}

    client = TestClient(app)
    results: list[int] = []

    def call():
        results.append(client.get("/work").status_code)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert sorted(results) == [200, 503]
