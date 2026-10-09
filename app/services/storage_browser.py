from app.schemas.storage import StorageBrowseResponse, StorageObjectRead
from app.services.storage import ObjectStorage


class StorageBrowserService:
    """Read-only browsing of the bucket (the admin storage page and the "import from storage" picker)."""

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    def browse(self, prefix: str, continuation_token: str | None) -> StorageBrowseResponse:
        page = self._storage.list_objects(prefix, continuation_token)
        objects = [
            StorageObjectRead(key=obj.key, size=obj.size, last_modified=obj.last_modified) for obj in page.objects
        ]
        return StorageBrowseResponse(prefix=prefix, prefixes=page.prefixes, objects=objects, next_token=page.next_token)

    def get_download_url(self, key: str) -> str:
        return self._storage.get_presigned_get_url(key)
