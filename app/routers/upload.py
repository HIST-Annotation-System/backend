import uuid

from fastapi import APIRouter, Depends, status

from app.dependencies.auth import AdminDep, UploaderDep, require_upload
from app.dependencies.resources import ResourcesDep
from app.dependencies.services import OrganizationServiceDep, SlideIngestServiceDep, SlideUploadServiceDep
from app.exceptions.errors import ForbiddenError
from app.models.account import Role
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

# Administrators and annotators given "can upload files" add slides (into their own organization; the system admin
# into any). Importing from the bucket is narrowed to the system admin below.
router = APIRouter(dependencies=[Depends(require_upload)])

GLOBAL_SLIDES_ADMIN_ONLY = "Only the system administrator can add slides for every organization."


@router.post("/start", response_model=StartUploadResponse)
def start_upload(
    req: StartUploadRequest,
    uploader: UploaderDep,
    service: SlideUploadServiceDep,
    organizations: OrganizationServiceDep,
) -> StartUploadResponse:
    organization_id: int | None
    if req.is_global:
        if uploader.role != Role.ADMIN:
            raise ForbiddenError(GLOBAL_SLIDES_ADMIN_ONLY)
        organization_id = None
    else:
        organization_id = organizations.target_for(uploader, req.organization_id)
    return service.start_upload(
        req.filename, uploader.id, req.file_size, req.allow_duplicate, organization_id=organization_id
    )


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
def cancel_upload(slide_id: uuid.UUID, service: SlideUploadServiceDep, user: UploaderDep) -> None:
    service.cancel_upload(slide_id, user)


# The bucket browser behind imports shows every organization's files, so importing stays with the system admin.
@router.post("/import-from-storage", response_model=ImportFromStorageResponse)
def import_from_storage(
    req: ImportFromStorageRequest,
    admin: AdminDep,
    service: SlideUploadServiceDep,
    ingest: SlideIngestServiceDep,
    resources: ResourcesDep,
    organizations: OrganizationServiceDep,
) -> ImportFromStorageResponse:
    organization_id = organizations.target_for(admin, req.organization_id)
    slide_id, created = service.import_from_storage(req.key, admin.id, organization_id=organization_id)
    if created:
        resources.ingest_pool.submit(ingest.ingest, slide_id)
    return ImportFromStorageResponse(slide_id=slide_id, created=created)
