"""S3 호환 객체 스토리지 다운로드 서비스."""

import os
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class S3ConfigurationError(RuntimeError):
    """객체 스토리지 실행에 필요한 환경 설정이 없는 경우."""


class S3DownloadError(RuntimeError):
    """객체 다운로드에 실패한 경우."""


class S3UploadError(RuntimeError):
    """객체 업로드에 실패한 경우."""


def get_configured_bucket() -> str:
    """설정된 버킷 이름을 반환한다. 없으면 설정 오류를 올린다."""

    bucket = os.getenv("AWS_S3_BUCKET")
    if not bucket:
        raise S3ConfigurationError(
            "AWS_S3_BUCKET 환경변수가 설정되지 않았습니다."
        )
    return bucket


def _artifact_kind(path: Path) -> str:
    """확장자로 산출물 종류를 판정한다."""

    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return "table"
    if suffix in {".png", ".jpg", ".jpeg", ".svg"}:
        return "figure"
    if suffix == ".json":
        return "json"
    return "other"


def _create_s3_client() -> Any:
    """로컬에서는 MinIO, 그 외 환경에서는 AWS S3 클라이언트를 생성한다."""

    region = os.getenv("AWS_REGION", "ap-northeast-2")
    minio_endpoint = os.getenv("MINIO_ENDPOINT")

    if not minio_endpoint:
        return boto3.client(
            "s3",
            region_name=region,
        )

    minio_access_key = os.getenv("MINIO_ROOT_USER")
    minio_secret_key = os.getenv("MINIO_ROOT_PASSWORD")

    if not minio_access_key or not minio_secret_key:
        raise S3ConfigurationError(
            "MinIO 사용 시 MINIO_ROOT_USER와 "
            "MINIO_ROOT_PASSWORD가 필요합니다."
        )

    return boto3.client(
        "s3",
        endpoint_url=minio_endpoint,
        aws_access_key_id=minio_access_key,
        aws_secret_access_key=minio_secret_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def download_s3_object(
    s3_key: str,
    destination: Path,
) -> Path:
    """S3 Key에 해당하는 객체를 지정된 로컬 경로로 다운로드한다."""

    bucket = os.getenv("AWS_S3_BUCKET")

    if not bucket:
        raise S3ConfigurationError(
            "AWS_S3_BUCKET 환경변수가 설정되지 않았습니다."
        )

    normalized_key = s3_key.strip()
    if not normalized_key:
        raise S3DownloadError("S3 Key는 비어 있을 수 없습니다.")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    s3_client = _create_s3_client()

    try:
        s3_client.download_file(
            bucket,
            normalized_key,
            str(destination),
        )
    except (BotoCoreError, ClientError) as exception:
        raise S3DownloadError(
            f"S3 객체 다운로드에 실패했습니다: {normalized_key}"
        ) from exception

    return destination


def upload_s3_object(source: Path, s3_key: str) -> str:
    """로컬 파일을 지정한 S3 Key로 업로드하고 Key를 돌려준다."""

    bucket = get_configured_bucket()

    normalized_key = s3_key.strip()
    if not normalized_key:
        raise S3UploadError("S3 Key는 비어 있을 수 없습니다.")

    source = Path(source)
    if not source.is_file():
        raise S3UploadError(f"업로드할 파일을 찾을 수 없습니다: {source}")

    s3_client = _create_s3_client()

    try:
        s3_client.upload_file(str(source), bucket, normalized_key)
    except (BotoCoreError, ClientError) as exception:
        raise S3UploadError(
            f"S3 객체 업로드에 실패했습니다: {normalized_key}"
        ) from exception

    return normalized_key


def upload_directory(
    local_dir: Path,
    key_prefix: str,
) -> list[dict[str, str]]:
    """디렉터리의 모든 파일을 접두사 아래로 업로드하고 목록을 돌려준다.

    반환 항목은 `{"name": 상대경로, "s3_key": 전체 Key, "kind": 종류}` 형식이다.
    한 파일이라도 실패하면 `S3UploadError` 를 올린다.
    """

    local_dir = Path(local_dir)
    if not local_dir.is_dir():
        raise S3UploadError(f"업로드할 디렉터리를 찾을 수 없습니다: {local_dir}")

    prefix = key_prefix.strip().strip("/")
    uploaded: list[dict[str, str]] = []

    for path in sorted(local_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(local_dir).as_posix()
        key = f"{prefix}/{relative}" if prefix else relative
        upload_s3_object(path, key)
        uploaded.append(
            {
                "name": relative,
                "s3_key": key,
                "kind": _artifact_kind(path),
            }
        )

    return uploaded