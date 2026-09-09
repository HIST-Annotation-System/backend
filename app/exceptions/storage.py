class StorageException(Exception):
    """Base exception for storage-related operations."""
    pass

class BucketConfigurationError(StorageException):
    """Raised when bucket cannot be configured or verified."""
    pass

class PresignedUrlError(StorageException):
    """Raised when presigned URL generation fails."""
    pass

class FileUploadError(StorageException):
    """Raised when a standard file upload fails."""
    pass

class MultipartUploadError(StorageException):
    """Raised when a multipart upload operation (start, part, complete) fails."""
    pass
