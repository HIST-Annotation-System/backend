from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.exceptions.errors import StorageError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__name__)


PRESIGNED_URL_EXPIRATION_SECONDS = 3600
STORAGE_UNAVAILABLE = "Object storage is unavailable."


class ObjectStorage(Protocol):
    """Object storage used by the services (MinIO/S3 in the app, a local fake in tests)."""

    def create_multipart_upload(self, key: str) -> str: ...

    def get_presigned_part_urls(self, key: str, upload_id: str, part_numbers: Iterable[int]) -> dict[int, str]: ...

    def complete_multipart_upload(self, key: str, upload_id: str, etags: dict[int, str]) -> None: ...

    def head_size(self, key: str) -> int: ...

    def download_file(self, key: str, path: Path) -> None: ...

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...


@contextmanager
def _storage_errors() -> Iterator[None]:
    """Log storage failures and turn them into StorageError (clients only get a generic message)."""
    try:
        yield
    except (BotoCoreError, ClientError) as exc:
        logger.error("Object storage operation failed: %s", exc)
        raise StorageError(STORAGE_UNAVAILABLE) from exc


def _create_client(settings: Settings, endpoint_url: str) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.BUCKET_ACCESS_KEY,
        aws_secret_access_key=settings.BUCKET_SECRET_KEY,
        region_name=settings.BUCKET_REGION,
        config=Config(
            signature_version="s3v4",
            # GCS's S3 API rejects the aws-chunked uploads with CRC32 trailers that boto3 sends by default.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


class ObjectStorageService:
    """MinIO/S3 storage. Presigned URLs are signed for the endpoint browsers reach (signing is offline)."""

    def __init__(self, settings: Settings) -> None:
        self._client = _create_client(settings, settings.BUCKET_ENDPOINT)
        self._presign_client = _create_client(settings, settings.BUCKET_PUBLIC_ENDPOINT or settings.BUCKET_ENDPOINT)
        self._bucket = settings.BUCKET_NAME

    def ensure_bucket(self) -> None:
        with _storage_errors():
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
                    raise
                self._client.create_bucket(Bucket=self._bucket)

    def create_multipart_upload(self, key: str) -> str:
        with _storage_errors():
            return self._client.create_multipart_upload(Bucket=self._bucket, Key=key)["UploadId"]

    def get_presigned_part_urls(self, key: str, upload_id: str, part_numbers: Iterable[int]) -> dict[int, str]:
        with _storage_errors():
            return {
                part: self._presign_client.generate_presigned_url(
                    ClientMethod="upload_part",
                    Params={"Bucket": self._bucket, "Key": key, "UploadId": upload_id, "PartNumber": part},
                    ExpiresIn=PRESIGNED_URL_EXPIRATION_SECONDS,
                )
                for part in part_numbers
            }

    def complete_multipart_upload(self, key: str, upload_id: str, etags: dict[int, str]) -> None:
        with _storage_errors():
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": [{"PartNumber": part, "ETag": etags[part]} for part in sorted(etags)]},
            )

    def head_size(self, key: str) -> int:
        with _storage_errors():
            return self._client.head_object(Bucket=self._bucket, Key=key)["ContentLength"]

    def download_file(self, key: str, path: Path) -> None:
        with _storage_errors():
            self._client.download_file(self._bucket, key, str(path))

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        with _storage_errors():
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        with _storage_errors():
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
