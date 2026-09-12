from fastapi import APIRouter, BackgroundTasks, status

from app.dependencies.services import SlideIngestServiceDep, SlideUploadServiceDep
from app.schemas.upload import (
    CompleteUploadRequest,
    PresignedUrlsRequest,
    PresignedUrlsResponse,
    RefreshUrlsRequest,
    RefreshUrlsResponse,
    StartUploadRequest,
    StartUploadResponse,
)

router = APIRouter()


@router.post("/start", response_model=StartUploadResponse)
def start_upload(req: StartUploadRequest, service: SlideUploadServiceDep) -> StartUploadResponse:
    return service.start_upload(req.filename)


@router.post("/presigned-urls", response_model=PresignedUrlsResponse)
def generate_presigned_urls(req: PresignedUrlsRequest, service: SlideUploadServiceDep) -> PresignedUrlsResponse:
    return service.presign_parts(req.slide_id, req.upload_id, req.total_parts)


@router.post("/refresh-urls", response_model=RefreshUrlsResponse)
def refresh_urls(req: RefreshUrlsRequest, service: SlideUploadServiceDep) -> RefreshUrlsResponse:
    return service.refresh_part_urls(req.slide_id, req.upload_id, req.pending_parts)


@router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
def complete_upload(
    req: CompleteUploadRequest,
    background_tasks: BackgroundTasks,
    service: SlideUploadServiceDep,
    ingest: SlideIngestServiceDep,
) -> None:
    service.complete_upload(req.slide_id, req.upload_id, {part.PartNumber: part.ETag for part in req.parts})
    background_tasks.add_task(ingest.ingest, req.slide_id)
