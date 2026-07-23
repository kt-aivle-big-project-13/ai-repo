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