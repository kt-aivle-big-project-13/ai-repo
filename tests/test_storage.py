"""S3 저장소 서비스 테스트."""

from pathlib import Path

import pytest

import app.services.storage as storage
from app.services.storage import S3ConfigurationError, S3UploadError


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


def test_upload_s3_object(monkeypatch, tmp_path):
    """설정된 버킷과 Key로 파일을 업로드한다."""

    calls = []

    class FakeS3Client:
        def upload_file(self, source, bucket, key):
            calls.append((source, bucket, key))

    monkeypatch.setenv("AWS_S3_BUCKET", "test-audit-bucket")
    monkeypatch.setattr(storage, "_create_s3_client", lambda: FakeS3Client())

    source = tmp_path / "report.csv"
    source.write_text("x", encoding="utf-8")

    key = storage.upload_s3_object(source, "reports/report.csv")

    assert key == "reports/report.csv"
    assert calls == [(str(source), "test-audit-bucket", "reports/report.csv")]


def test_upload_directory_uploads_all_files_with_prefix(monkeypatch, tmp_path):
    """디렉터리 전체를 접두사 아래로 올리고 종류를 판정한다."""

    class FakeS3Client:
        def upload_file(self, source, bucket, key):
            pass

    monkeypatch.setenv("AWS_S3_BUCKET", "bucket")
    monkeypatch.setattr(storage, "_create_s3_client", lambda: FakeS3Client())

    output = tmp_path / "outputs"
    (output / "global").mkdir(parents=True)
    (output / "global" / "importance.csv").write_text("x", encoding="utf-8")
    (output / "figures").mkdir()
    (output / "figures" / "chart.png").write_bytes(b"x")
    (output / "summary.json").write_text("{}", encoding="utf-8")

    result = storage.upload_directory(output, "explainability/1/run/")

    keys = {item["s3_key"] for item in result}
    assert keys == {
        "explainability/1/run/global/importance.csv",
        "explainability/1/run/figures/chart.png",
        "explainability/1/run/summary.json",
    }
    kinds = {item["name"]: item["kind"] for item in result}
    assert kinds["global/importance.csv"] == "table"
    assert kinds["figures/chart.png"] == "figure"
    assert kinds["summary.json"] == "json"


def test_upload_fails_when_bucket_is_missing(monkeypatch, tmp_path):
    """버킷 설정이 없으면 업로드 전에 실패한다."""

    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    source = tmp_path / "report.csv"
    source.write_text("x", encoding="utf-8")

    with pytest.raises(S3ConfigurationError, match="AWS_S3_BUCKET"):
        storage.upload_s3_object(source, "reports/report.csv")


def test_upload_raises_on_client_error(monkeypatch, tmp_path):
    """업로드 호출이 실패하면 S3UploadError로 감싼다."""

    from botocore.exceptions import ClientError

    class FakeS3Client:
        def upload_file(self, source, bucket, key):
            raise ClientError({"Error": {"Code": "500"}}, "PutObject")

    monkeypatch.setenv("AWS_S3_BUCKET", "bucket")
    monkeypatch.setattr(storage, "_create_s3_client", lambda: FakeS3Client())

    source = tmp_path / "report.csv"
    source.write_text("x", encoding="utf-8")

    with pytest.raises(S3UploadError):
        storage.upload_s3_object(source, "reports/report.csv")
