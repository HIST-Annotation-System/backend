import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.exceptions.errors import StorageError, StorageObjectMissing
from app.services.storage import _storage_errors


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
