import boto3
from typing import Generator
from app.core.config import settings

def get_s3_client() -> Generator:
    """Dependency to provide a boto3 S3 client."""
    s3_client = boto3.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1"
    )
    try:
        yield s3_client
    finally:
        pass
