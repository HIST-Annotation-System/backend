import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.core.config import Settings
from app.exceptions.errors import StorageError, StorageObjectMissing
from app.services.storage import ObjectStorageService, _storage_errors


def client_error(code: str, status: int) -> ClientError:
    return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "HeadObject")


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NoSuchUpload"])
def test_a_missing_object_is_reported_as_missing(code: str) -> None:
    with pytest.raises(StorageObjectMissing), _storage_errors():
        raise client_error(code, 404)


@pytest.mark.parametrize(
    "error",
    [
        client_error("NoSuchBucket", 404),
        client_error("InternalError", 500),
        client_error("AccessDenied", 403),
        EndpointConnectionError(endpoint_url="http://s3"),
    ],
)
def test_other_failures_are_an_outage(error: Exception) -> None:
    with pytest.raises(StorageError) as raised, _storage_errors():
        raise error
    assert type(raised.value) is StorageError


def test_presigned_urls_point_where_the_browser_reaches_the_bucket(settings: Settings) -> None:
    settings.BUCKET_ENDPOINT = "http://s3:9090"
    settings.BUCKET_PUBLIC_ENDPOINT = "http://localhost:9090"
    storage = ObjectStorageService(settings)
    assert storage.get_presigned_get_url("a/b.svs").startswith("http://localhost:9090/")
    assert storage.get_presigned_part_urls("a/b.svs", "upload-1", [1])[1].startswith("http://localhost:9090/")


def test_without_a_public_endpoint_presigned_urls_use_the_bucket_endpoint(settings: Settings) -> None:
    settings.BUCKET_ENDPOINT = "https://s3.eu-north-1.amazonaws.com"
    assert ObjectStorageService(settings).get_presigned_get_url("a/b.svs").startswith(
        "https://s3.eu-north-1.amazonaws.com/"
    )
