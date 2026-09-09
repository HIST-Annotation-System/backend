import logging
from botocore.exceptions import ClientError
from app.core.config import settings
from app.exceptions.storage import (
    BucketConfigurationError,
    PresignedUrlError,
    FileUploadError,
    MultipartUploadError,
)

logger = logging.getLogger(__name__)

class StorageService:
    def __init__(self, s3_client):
        self.s3_client = s3_client
        self.bucket_name = settings.S3_BUCKET_NAME
        
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self) -> None:
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                logger.info(f"Bucket {self.bucket_name} does not exist. Creating it...")
                self.s3_client.create_bucket(Bucket=self.bucket_name)
            else:
                logger.error(f"Error checking/creating bucket {self.bucket_name}: {e}")
                raise BucketConfigurationError(str(e))

    def get_presigned_url(self, object_name: str, expiration: int = 3600) -> str:
        """
        Generate a presigned URL to share an S3 object.
        Used for frontend to access patches.
        """
        try:
            response = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': object_name
                },
                ExpiresIn=expiration
            )
            return response
        except ClientError as e:
            logger.error(f"Error generating presigned URL for {object_name}: {e}")
            raise PresignedUrlError(str(e))

    def upload_file(self, file_path: str, object_name: str) -> bool:
        """Uploads file to a bucket."""
        try:
            self.s3_client.upload_file(file_path, self.bucket_name, object_name)
            return True
        except ClientError as e:
            logger.error(f"Error uploading {file_path} to {object_name}: {e}")
            raise FileUploadError(str(e))

class StorageUploadService:
    def __init__(self, s3_client):
        self.s3_client = s3_client
        self.bucket_name = settings.S3_BUCKET_NAME
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self) -> None:
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                logger.info(f"Bucket {self.bucket_name} does not exist. Creating it...")
                self.s3_client.create_bucket(Bucket=self.bucket_name)
            else:
                logger.error(f"Error checking/creating bucket {self.bucket_name}: {e}")

    def _fix_url(self, url: str) -> str:
        if "http://minio:9000" in url:
            return url.replace("http://minio:9000", "http://127.0.0.1:9000")
        return url

    def create_multipart_upload(self, object_name: str) -> str:
        try:
            response = self.s3_client.create_multipart_upload(
                Bucket=self.bucket_name,
                Key=object_name
            )
            return response['UploadId']
        except ClientError as e:
            logger.error(f"Error creating multipart upload for {object_name}: {e}")
            raise MultipartUploadError(str(e))

    def get_presigned_urls_for_multipart(self, object_name: str, upload_id: str, total_parts: int, expiration: int = 3600) -> list[str]:
        urls = []
        for part_number in range(1, total_parts + 1):
            try:
                url = self.s3_client.generate_presigned_url(
                    ClientMethod='upload_part',
                    Params={
                        'Bucket': self.bucket_name,
                        'Key': object_name,
                        'UploadId': upload_id,
                        'PartNumber': part_number
                    },
                    ExpiresIn=expiration
                )
                urls.append(self._fix_url(url))
            except ClientError as e:
                logger.error(f"Error generating presigned URL for part {part_number} of {object_name}: {e}")
                raise PresignedUrlError(str(e))
        return urls

    def refresh_presigned_urls_for_parts(self, object_name: str, upload_id: str, pending_parts: list[int], expiration: int = 3600) -> dict[int, str]:
        urls = {}
        for part_number in pending_parts:
            try:
                url = self.s3_client.generate_presigned_url(
                    ClientMethod='upload_part',
                    Params={
                        'Bucket': self.bucket_name,
                        'Key': object_name,
                        'UploadId': upload_id,
                        'PartNumber': part_number
                    },
                    ExpiresIn=expiration
                )
                urls[part_number] = self._fix_url(url)
            except ClientError as e:
                logger.error(f"Error refreshing presigned URL for part {part_number} of {object_name}: {e}")
                raise PresignedUrlError(str(e))
        return urls

    def complete_multipart_upload(self, object_name: str, upload_id: str, parts: list[dict]) -> dict:
        try:
            sorted_parts = sorted(parts, key=lambda i: i['PartNumber'])
            response = self.s3_client.complete_multipart_upload(
                Bucket=self.bucket_name,
                Key=object_name,
                UploadId=upload_id,
                MultipartUpload={'Parts': sorted_parts}
            )
            return response
        except ClientError as e:
            logger.error(f"Error completing multipart upload for {object_name}: {e}")
            raise MultipartUploadError(str(e))
