from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
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
# Generous enough that a single request covers a normal-sized bucket level; the browser pages via next_token.
BROWSE_PAGE_SIZE = 500


@dataclass(frozen=True)
class StorageObject:
    key: str
    size: int
    last_modified: datetime


@dataclass(frozen=True)
class StoragePage:
    """One level of a `list_objects` browse: subfolders (common prefixes) and objects."""

    prefixes: list[str]
    objects: list[StorageObject]
    next_token: str | None


@dataclass(frozen=True)
class StoredPart:
    number: int
    etag: str
    size: int


class ObjectStorage(Protocol):
    """Object storage used by the services (S3 in the app, a local fake in tests)."""

    def create_multipart_upload(self, key: str) -> str: ...

    def get_presigned_part_urls(self, key: str, upload_id: str, part_numbers: Iterable[int]) -> dict[int, str]: ...

    def complete_multipart_upload(self, key: str, upload_id: str, etags: dict[int, str]) -> None: ...

    def list_parts(self, key: str, upload_id: str) -> list[StoredPart]: ...

    def head_size(self, key: str) -> int: ...

    def download_file(self, key: str, path: Path, on_progress: Callable[[int], None] | None = None) -> None: ...

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def list_objects(self, prefix: str, continuation_token: str | None) -> StoragePage: ...

    def get_presigned_get_url(self, key: str) -> str: ...

    def iter_keys(self, prefix: str) -> Iterator[StorageObject]: ...

    def delete_objects(self, keys: Iterable[str]) -> None: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def abort_multipart_uploads(self, key: str) -> None: ...


@contextmanager
def _storage_errors() -> Generator[None]:
    """Log storage failures and turn them into StorageError (clients only get a generic message)."""
    try:
        yield
    except (BotoCoreError, ClientError) as exc:
        logger.error("Object storage operation failed: %s", exc)
        raise StorageError(STORAGE_UNAVAILABLE) from exc


def _create_client(settings: Settings) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=settings.BUCKET_ENDPOINT,
        aws_access_key_id=settings.BUCKET_ACCESS_KEY,
        aws_secret_access_key=settings.BUCKET_SECRET_KEY,
        region_name=settings.BUCKET_REGION,
        config=Config(signature_version="s3v4"),
    )


class ObjectStorageService:
    """S3 storage. Presigning is offline, so the browser uploads parts straight to the bucket."""

    def __init__(self, settings: Settings) -> None:
        self._client = _create_client(settings)
        self._bucket = settings.BUCKET_NAME

    def check_bucket(self) -> None:
        """Logs a clear error when the bucket is missing or unreachable.

        The bucket is provisioned outside the application (and never created here), and a storage
        outage must not stop the API from starting: failures surface per request instead.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except (BotoCoreError, ClientError) as exc:
            logger.error("Bucket %s is not reachable, uploads and slides will fail: %s", self._bucket, exc)

    def create_multipart_upload(self, key: str) -> str:
        with _storage_errors():
            return self._client.create_multipart_upload(Bucket=self._bucket, Key=key)["UploadId"]

    def get_presigned_part_urls(self, key: str, upload_id: str, part_numbers: Iterable[int]) -> dict[int, str]:
        with _storage_errors():
            return {
                part: self._client.generate_presigned_url(
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

    def list_parts(self, key: str, upload_id: str) -> list[StoredPart]:
        """Parts already uploaded to an in-progress multipart upload (NoSuchUpload once it is aborted)."""
        with _storage_errors():
            pages = self._client.get_paginator("list_parts").paginate(Bucket=self._bucket, Key=key, UploadId=upload_id)
            return [
                StoredPart(number=part["PartNumber"], etag=part["ETag"].strip('"'), size=part["Size"])
                for page in pages
                for part in page.get("Parts", [])
            ]

    def head_size(self, key: str) -> int:
        with _storage_errors():
            return self._client.head_object(Bucket=self._bucket, Key=key)["ContentLength"]

    def download_file(self, key: str, path: Path, on_progress: Callable[[int], None] | None = None) -> None:
        # boto3 calls `on_progress` with the byte count transferred *since the last call* (a delta,
        # not a running total), from whichever worker thread happened to finish that chunk.
        with _storage_errors():
            self._client.download_file(self._bucket, key, str(path), Callback=on_progress)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        with _storage_errors():
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        with _storage_errors():
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def list_objects(self, prefix: str, continuation_token: str | None) -> StoragePage:
        with _storage_errors():
            if continuation_token:
                response = self._client.list_objects_v2(
                    Bucket=self._bucket,
                    Prefix=prefix,
                    Delimiter="/",
                    MaxKeys=BROWSE_PAGE_SIZE,
                    ContinuationToken=continuation_token,
                )
            else:
                response = self._client.list_objects_v2(
                    Bucket=self._bucket, Prefix=prefix, Delimiter="/", MaxKeys=BROWSE_PAGE_SIZE
                )
            objects = [
                StorageObject(key=item["Key"], size=item["Size"], last_modified=item["LastModified"])
                for item in response.get("Contents", [])
                # A folder created in the S3 console is a zero-byte object named after the prefix itself.
                if item["Key"] != prefix
            ]
            prefixes = [item["Prefix"] for item in response.get("CommonPrefixes", [])]
            next_token = response.get("NextContinuationToken") if response.get("IsTruncated") else None
            return StoragePage(prefixes=prefixes, objects=objects, next_token=next_token)

    def get_presigned_get_url(self, key: str) -> str:
        with _storage_errors():
            return self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=PRESIGNED_URL_EXPIRATION_SECONDS,
            )

    def iter_keys(self, prefix: str) -> Iterator[StorageObject]:
        """Every object under `prefix`, at any depth (unlike the one-level `list_objects` browse)."""
        with _storage_errors():
            for page in self._client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    # A folder created in the S3 console is a zero-byte object named after the prefix itself.
                    if item["Key"] != prefix:
                        yield StorageObject(key=item["Key"], size=item["Size"], last_modified=item["LastModified"])

    def delete_objects(self, keys: Iterable[str]) -> None:
        """Best-effort: a failure on one key (e.g. a race with an in-flight download of it) is logged,
        not raised, so cleanup never blocks the caller from deleting the record that owned the file.
        """
        for key in keys:
            try:
                self._client.delete_object(Bucket=self._bucket, Key=key)
            except (BotoCoreError, ClientError) as exc:
                logger.warning("Failed to delete object %s: %s", key, exc)

    def delete_prefix(self, prefix: str) -> None:
        """Best-effort deletion of every object under `prefix`, like `delete_objects`.

        Keys are listed up front, so deleting never shifts the listing under the paginator.
        """
        try:
            keys = [obj.key for obj in self.iter_keys(prefix)]
        except StorageError:
            return
        self.delete_objects(keys)

    def abort_multipart_uploads(self, key: str) -> None:
        """Aborts any in-progress multipart upload for `key` (used when a browser upload is canceled).

        Best-effort, like `delete_objects`: canceling should always let the caller drop the slide
        record, even if the upload already completed or expired on the storage side.
        """
        try:
            response = self._client.list_multipart_uploads(Bucket=self._bucket, Prefix=key)
            for upload in response.get("Uploads", []):
                if upload["Key"] == key:
                    self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload["UploadId"])
        except (BotoCoreError, ClientError) as exc:
            logger.warning("Failed to abort multipart upload(s) for %s: %s", key, exc)
