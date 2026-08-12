"""헬스체크 엔드포인트 테스트.

ALB 타깃 그룹이 이 응답으로 인스턴스 교체 여부를 판단하므로, 상태코드와 본문을
계약으로 고정한다.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_depend_on_external_configuration(monkeypatch):
    """S3 설정이 없어도 200 이어야 한다.

    외부 의존성 장애로 unhealthy 가 되면 ASG 가 멀쩡한 인스턴스를 계속 교체하고,
    교체된 인스턴스도 같은 이유로 실패해 복구되지 않는다.
    """

    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert client.get("/health").status_code == 200
