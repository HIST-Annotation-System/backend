from datetime import datetime

from pydantic import BaseModel


class StorageObjectRead(BaseModel):
    key: str
    size: int
    last_modified: datetime


class StorageBrowseResponse(BaseModel):
    prefix: str
    prefixes: list[str]
    objects: list[StorageObjectRead]
    next_token: str | None


class DownloadUrlResponse(BaseModel):
    url: str
