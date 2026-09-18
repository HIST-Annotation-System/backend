import uuid
from typing import Annotated

from pydantic import BaseModel, Field

from app.core.constants import NAME_MAX_LENGTH, NAME_MIN_LENGTH, STORAGE_KEY_MAX_LENGTH

MULTIPART_UPLOAD_MAX_PARTS = 10_000

PartNumber = Annotated[int, Field(gt=0, le=MULTIPART_UPLOAD_MAX_PARTS)]


class StartUploadRequest(BaseModel):
    filename: str = Field(min_length=NAME_MIN_LENGTH, max_length=NAME_MAX_LENGTH)


class StartUploadResponse(BaseModel):
    slide_id: uuid.UUID
    upload_id: str


class PresignedUrlsRequest(BaseModel):
    slide_id: uuid.UUID
    upload_id: str
    total_parts: PartNumber


class PresignedUrlsResponse(BaseModel):
    urls: list[str]


class RefreshUrlsRequest(BaseModel):
    slide_id: uuid.UUID
    upload_id: str
    pending_parts: list[PartNumber] = Field(min_length=1)


class RefreshUrlsResponse(BaseModel):
    urls: dict[int, str]


class UploadPart(BaseModel):
    PartNumber: PartNumber
    ETag: str


class CompleteUploadRequest(BaseModel):
    slide_id: uuid.UUID
    upload_id: str
    parts: list[UploadPart] = Field(min_length=1)


class ImportFromStorageRequest(BaseModel):
    key: str = Field(min_length=NAME_MIN_LENGTH, max_length=STORAGE_KEY_MAX_LENGTH)


class ImportFromStorageResponse(BaseModel):
    slide_id: uuid.UUID
