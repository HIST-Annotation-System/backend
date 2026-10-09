import uuid

from fastapi import APIRouter, Depends, status

from app.dependencies.auth import AdminDep, require_admin
from app.dependencies.resources import ResourcesDep
from app.dependencies.services import SlideIngestServiceDep, SlideUploadServiceDep
from app.schemas.upload import (
    CompleteUploadRequest,
    ImportFromStorageRequest,
    ImportFromStorageResponse,
    PresignedUrlsRequest,
    PresignedUrlsResponse,
    RefreshUrlsRequest,
    RefreshUrlsResponse,
    StartUploadRequest,
    StartUploadResponse,
    UploadedPartsResponse,
    UploadProgressRequest,
)

# Adding slides is an administrator task.
router = APIRouter(dependencies=[Depends(require_admin)])


@router.post("/start", response_model=StartUploadResponse)
def start_upload(req: StartUploadRequest, admin: AdminDep, service: SlideUploadServiceDep) -> StartUploadResponse:
    return service.start_upload(req.filename, admin.id, req.file_size, req.allow_duplicate)


@router.post("/presigned-urls", response_model=PresignedUrlsResponse)
def generate_presigned_urls(req: PresignedUrlsRequest, service: SlideUploadServiceDep) -> PresignedUrlsResponse:
    return service.presign_parts(req.slide_id, req.upload_id, req.total_parts)


@router.post("/refresh-urls", response_model=RefreshUrlsResponse)
def refresh_urls(req: RefreshUrlsRequest, service: SlideUploadServiceDep) -> RefreshUrlsResponse:
    return service.refresh_part_urls(req.slide_id, req.upload_id, req.pending_parts)


@router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
def complete_upload(
    req: CompleteUploadRequest,
    service: SlideUploadServiceDep,
    ingest: SlideIngestServiceDep,
    resources: ResourcesDep,
) -> None:
    service.complete_upload(req.slide_id, req.upload_id, {part.PartNumber: part.ETag for part in req.parts})
    resources.ingest_pool.submit(ingest.ingest, req.slide_id)


@router.post("/{slide_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
def report_upload_progress(slide_id: uuid.UUID, req: UploadProgressRequest, service: SlideUploadServiceDep) -> None:
    service.report_progress(slide_id, req.percent)


@router.get("/{slide_id}/parts", response_model=UploadedPartsResponse)
def list_uploaded_parts(slide_id: uuid.UUID, upload_id: str, service: SlideUploadServiceDep) -> UploadedPartsResponse:
    return service.list_uploaded_parts(slide_id, upload_id)


@router.post("/{slide_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_upload(slide_id: uuid.UUID, service: SlideUploadServiceDep) -> None:
    service.cancel_upload(slide_id)


@router.post("/import-from-storage", response_model=ImportFromStorageResponse)
def import_from_storage(
    req: ImportFromStorageRequest,
    admin: AdminDep,
    service: SlideUploadServiceDep,
    ingest: SlideIngestServiceDep,
    resources: ResourcesDep,
) -> ImportFromStorageResponse:
    slide_id, created = service.import_from_storage(req.key, admin.id)
    if created:
        resources.ingest_pool.submit(ingest.ingest, slide_id)
    return ImportFromStorageResponse(slide_id=slide_id, created=created)
