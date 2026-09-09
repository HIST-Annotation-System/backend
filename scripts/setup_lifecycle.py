"""
Setup lifecycle for MinIO bucket so 
incomplete multipart uploads are aborted after 3 days.
Run this script with `python scripts/setup_lifecycle.py` after starting MinIO.
"""

import boto3
import os
import sys

endpoint_url = os.environ.get("MINIO_URL", "http://localhost:9000")
access_key = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
secret_key = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
bucket_name = os.environ.get("S3_BUCKET_NAME", "wsi-images")

def setup_lifecycle():
    s3_client = boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1"
    )

    lifecycle_configuration = {
        'Rules': [
            {
                'ID': 'AbortIncompleteMultipartUploads',
                'Filter': {'Prefix': ''},
                'Status': 'Enabled',
                'AbortIncompleteMultipartUpload': {
                    'DaysAfterInitiation': 3
                }
            }
        ]
    }

    try:
        s3_client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_configuration
        )
        print(f"Successfully configured lifecycle rule on bucket '{bucket_name}' to abort incomplete multipart uploads after 3 days.")
    except Exception as e:
        print(f"Error configuring lifecycle policy: {e}")
        sys.exit(1)

if __name__ == "__main__":
    setup_lifecycle()
