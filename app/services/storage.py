"""S3 객체 다운로드 서비스."""

import os
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class S3ConfigurationError(RuntimeError):
    """S3 실행에 필요한 환경 설정이 없는 경우."""


class S3DownloadError(RuntimeError):
    """S3 객체 다운로드에 실패한 경우."""


def download_s3_object(
    s3_key: str,
    destination: Path,
) -> Path:
    """S3 Key에 해당하는 객체를 지정된 로컬 경로로 다운로드한다."""

    bucket = os.getenv("AWS_S3_BUCKET")
    region = os.getenv("AWS_REGION", "ap-northeast-2")

    if not bucket:
        raise S3ConfigurationError(
            "AWS_S3_BUCKET 환경변수가 설정되지 않았습니다."
        )

    normalized_key = s3_key.strip()
    if not normalized_key:
        raise S3DownloadError("S3 Key는 비어 있을 수 없습니다.")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    s3_client = boto3.client(
        "s3",
        region_name=region,
    )

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
