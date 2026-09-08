import logging
from botocore.exceptions import ClientError
from app.core.config import settings

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
                raise e

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
            raise

    def upload_file(self, file_path: str, object_name: str) -> bool:
        """Uploads file to a bucket."""
        try:
            self.s3_client.upload_file(file_path, self.bucket_name, object_name)
            return True
        except ClientError as e:
            logger.error(f"Error uploading {file_path} to {object_name}: {e}")
            return False
