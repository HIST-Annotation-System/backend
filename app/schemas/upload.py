from pydantic import BaseModel
from typing import List

class StartUploadRequest(BaseModel):
    filename: str

class StartUploadResponse(BaseModel):
    upload_id: str
    key: str

class PresignedUrlsRequest(BaseModel):
    upload_id: str
    key: str
    total_parts: int

class PresignedUrlsResponse(BaseModel):
    urls: List[str]

class RefreshUrlsRequest(BaseModel):
    upload_id: str
    key: str
    pending_parts: List[int]

class RefreshUrlsResponse(BaseModel):
    urls: dict[int, str]

class UploadPart(BaseModel):
    PartNumber: int
    ETag: str

class CompleteUploadRequest(BaseModel):
    upload_id: str
    key: str
    parts: List[UploadPart]

class CompleteUploadResponse(BaseModel):
    success: bool
    message: str
