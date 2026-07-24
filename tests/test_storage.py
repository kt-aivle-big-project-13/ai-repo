"""S3 저장소 서비스 테스트."""

from pathlib import Path

import pytest

import app.services.storage as storage
from app.services.storage import S3ConfigurationError


def test_download_s3_object(monkeypatch, tmp_path):
    """설정된 버킷과 Key로 객체를 다운로드한다."""

    calls = []

    class FakeS3Client:
        def download_file(
            self,
            bucket: str,
            key: str,
            destination: str,
        ):
            calls.append((bucket, key, destination))
            Path(destination).write_bytes(b"model-data")

    monkeypatch.delenv(
        "MINIO_ENDPOINT",
        raising=False,
    )

    monkeypatch.setenv(
        "AWS_S3_BUCKET",
        "test-audit-bucket",
    )
    monkeypatch.setenv(
        "AWS_REGION",
        "ap-northeast-2",
    )
    monkeypatch.setattr(
        storage.boto3,
        "client",
        lambda service_name, region_name: FakeS3Client(),
    )

    destination = tmp_path / "model.json"

    result = storage.download_s3_object(
        "models/model.json",
        destination,
    )

    assert result == destination
    assert destination.read_bytes() == b"model-data"
    assert calls == [
        (
            "test-audit-bucket",
            "models/model.json",
            str(destination),
        )
    ]


def test_download_fails_when_bucket_is_missing(
    monkeypatch,
    tmp_path,
):
    """버킷 설정이 없으면 외부 호출 전에 실패한다."""

    monkeypatch.delenv(
        "AWS_S3_BUCKET",
        raising=False,
    )

    with pytest.raises(
        S3ConfigurationError,
        match="AWS_S3_BUCKET",
    ):
        storage.download_s3_object(
            "models/model.json",
            tmp_path / "model.json",
        )

def test_create_s3_client_uses_aws_when_minio_is_not_configured(
    monkeypatch,
):
    """MinIO 설정이 없으면 AWS S3 기본 클라이언트를 생성한다."""

    captured = {}
    expected_client = object()

    monkeypatch.setenv(
        "AWS_REGION",
        "ap-northeast-2",
    )
    monkeypatch.delenv(
        "MINIO_ENDPOINT",
        raising=False,
    )

    def fake_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return expected_client

    monkeypatch.setattr(
        storage.boto3,
        "client",
        fake_client,
    )

    result = storage._create_s3_client()

    assert result is expected_client
    assert captured == {
        "service_name": "s3",
        "kwargs": {
            "region_name": "ap-northeast-2",
        },
    }


def test_create_s3_client_uses_minio_configuration(
    monkeypatch,
):
    """MinIO 설정이 있으면 endpoint, 인증정보와 path-style을 적용한다."""

    captured = {}
    expected_client = object()

    monkeypatch.setenv(
        "MINIO_ENDPOINT",
        "http://localhost:9000",
    )
    monkeypatch.setenv(
        "MINIO_ROOT_USER",
        "minio-user",
    )
    monkeypatch.setenv(
        "MINIO_ROOT_PASSWORD",
        "minio-password",
    )

    def fake_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return expected_client

    monkeypatch.setattr(
        storage.boto3,
        "client",
        fake_client,
    )

    result = storage._create_s3_client()

    assert result is expected_client
    assert captured["service_name"] == "s3"
    assert captured["kwargs"]["endpoint_url"] == (
        "http://localhost:9000"
    )
    assert captured["kwargs"]["aws_access_key_id"] == "minio-user"
    assert captured["kwargs"]["aws_secret_access_key"] == (
        "minio-password"
    )
    assert captured["kwargs"]["region_name"] == "us-east-1"
    assert captured["kwargs"]["config"].s3 == {
        "addressing_style": "path",
    }


@pytest.mark.parametrize(
    ("missing_variable",),
    [
        ("MINIO_ROOT_USER",),
        ("MINIO_ROOT_PASSWORD",),
    ],
)
def test_create_s3_client_fails_when_minio_credentials_are_missing(
    monkeypatch,
    missing_variable,
):
    """MinIO endpoint만 있고 인증정보가 불완전하면 실패한다."""

    monkeypatch.setenv(
        "MINIO_ENDPOINT",
        "http://localhost:9000",
    )
    monkeypatch.setenv(
        "MINIO_ROOT_USER",
        "minio-user",
    )
    monkeypatch.setenv(
        "MINIO_ROOT_PASSWORD",
        "minio-password",
    )
    monkeypatch.delenv(
        missing_variable,
        raising=False,
    )

    with pytest.raises(
        S3ConfigurationError,
        match="MINIO_ROOT_USER.*MINIO_ROOT_PASSWORD",
    ):
        storage._create_s3_client()
