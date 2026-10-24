import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse

from app.core.constants import CONTENT_DISPOSITION_ATTACHMENT, MEDIA_TYPE_GEOJSON
from app.dependencies.auth import CurrentUserDep
from app.dependencies.resources import ResourcesDep
from app.dependencies.services import (
    HotspotAnnotationServiceDep,
    HotspotServiceDep,
    TagServiceDep,
    run_hotspot_batch,
)
from app.schemas.annotations import AnnotationCreate, AnnotationRead, AnnotationUpdate
from app.schemas.hotspots import (
    HotspotBatchCreate,
    HotspotBatchRead,
    HotspotFolderRead,
    HotspotFolderWrite,
    HotspotRead,
    HotspotSort,
    HotspotStatus,
    HotspotUpdate,
    SortOrder,
)
from app.schemas.tags import HotspotTagsBulk
from app.services.hotspot_images import UPLOAD_MAX_BYTES
from app.services.slide_analysis import MEDIA_TYPE_JPEG

HOTSPOT_ANNOTATIONS_EXPORT_FILENAME = "annotations_hotspot_{hotspot_id}.geojson"
# A hotspot image never changes for a given id.
IMMUTABLE_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}

router = APIRouter()


@router.get("/hotspot-folders", response_model=list[HotspotFolderRead])
def list_folders(service: HotspotServiceDep) -> list[HotspotFolderRead]:
    return service.list_folders()


@router.post("/hotspot-folders", response_model=HotspotFolderRead, status_code=status.HTTP_201_CREATED)
def create_folder(data: HotspotFolderWrite, service: HotspotServiceDep) -> HotspotFolderRead:
    return service.create_folder(data.name)


@router.patch("/hotspot-folders/{folder_id}", response_model=HotspotFolderRead)
def rename_folder(folder_id: uuid.UUID, data: HotspotFolderWrite, service: HotspotServiceDep) -> HotspotFolderRead:
    return service.rename_folder(folder_id, data.name)


@router.delete("/hotspot-folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(folder_id: uuid.UUID, service: HotspotServiceDep) -> None:
    service.delete_folder(folder_id)


@router.post(
    "/hotspot-folders/{folder_id}/images", response_model=HotspotRead, status_code=status.HTTP_201_CREATED
)
def upload_hotspot_image(folder_id: uuid.UUID, file: UploadFile, service: HotspotServiceDep) -> HotspotRead:
    """One PNG/JPEG file becomes a hotspot in the folder (the browser uploads several files one by one)."""
    # One byte past the limit is enough to tell an oversized file apart without reading all of it.
    data = file.file.read(UPLOAD_MAX_BYTES + 1)
    return service.upload_image(folder_id, file.filename or "", data)


@router.get("/hotspots", response_model=list[HotspotRead])
def list_hotspots(
    service: HotspotServiceDep,
    folder_id: uuid.UUID | None = None,
    slide_id: uuid.UUID | None = None,
    sort: HotspotSort = HotspotSort.CREATED_AT,
    order: SortOrder = SortOrder.DESC,
    review_status: Annotated[HotspotStatus | None, Query(alias="status")] = None,
    tag_ids: Annotated[list[int] | None, Query()] = None,
) -> list[HotspotRead]:
    """All folders when `folder_id` is left out; `tag_ids` keeps hotspots that carry every listed tag."""
    return service.list_hotspots(folder_id, sort, order, slide_id, review_status, tag_ids or ())


@router.post("/hotspots/tags/bulk", status_code=status.HTTP_204_NO_CONTENT)
def tag_hotspots(data: HotspotTagsBulk, service: TagServiceDep, user: CurrentUserDep) -> None:
    service.tag_hotspots(data.hotspot_ids, data.add, data.remove, user)


@router.post("/hotspot-batches", response_model=HotspotBatchRead, status_code=status.HTTP_202_ACCEPTED)
def create_hotspot_batch(
    data: HotspotBatchCreate, service: HotspotServiceDep, resources: ResourcesDep
) -> HotspotBatchRead:
    """Start cutting slide squares out into hotspots; a retry with the same id returns that batch."""
    batch, created = service.create_batch(data)
    if created:
        resources.hotspot_pool.submit(run_hotspot_batch, resources, batch.id)
    return batch


@router.get("/hotspot-batches", response_model=list[HotspotBatchRead])
def list_hotspot_batches(service: HotspotServiceDep) -> list[HotspotBatchRead]:
    return service.list_batches()


@router.get("/hotspot-batches/{batch_id}", response_model=HotspotBatchRead)
def get_hotspot_batch(batch_id: uuid.UUID, service: HotspotServiceDep) -> HotspotBatchRead:
    return service.get_batch(batch_id)


@router.get("/hotspots/{hotspot_id}", response_model=HotspotRead)
def get_hotspot(hotspot_id: uuid.UUID, service: HotspotServiceDep) -> HotspotRead:
    return service.get_hotspot(hotspot_id)


@router.patch("/hotspots/{hotspot_id}", response_model=HotspotRead)
def update_hotspot(
    hotspot_id: uuid.UUID, data: HotspotUpdate, service: HotspotServiceDep, user: CurrentUserDep
) -> HotspotRead:
    return service.update_hotspot(hotspot_id, data, user)


@router.delete("/hotspots/{hotspot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_hotspot(hotspot_id: uuid.UUID, service: HotspotServiceDep) -> None:
    service.delete_hotspot(hotspot_id)


@router.put("/hotspots/{hotspot_id}/tags/{tag_id}", response_model=HotspotRead)
def add_hotspot_tag(hotspot_id: uuid.UUID, tag_id: int, service: TagServiceDep, user: CurrentUserDep) -> HotspotRead:
    """A core tag replaces the hotspot's tag of the same dimension."""
    (hotspot,) = service.tag_hotspots([hotspot_id], [tag_id], [], user)
    return HotspotRead.model_validate(hotspot)


@router.delete("/hotspots/{hotspot_id}/tags/{tag_id}", response_model=HotspotRead)
def remove_hotspot_tag(
    hotspot_id: uuid.UUID, tag_id: int, service: TagServiceDep, user: CurrentUserDep
) -> HotspotRead:
    (hotspot,) = service.tag_hotspots([hotspot_id], [], [tag_id], user)
    return HotspotRead.model_validate(hotspot)


@router.get("/hotspots/{hotspot_id}/image")
def get_hotspot_image(hotspot_id: uuid.UUID, service: HotspotServiceDep) -> Response:
    data, media_type = service.get_image(hotspot_id)
    return Response(data, media_type=media_type, headers=IMMUTABLE_CACHE_HEADERS)


@router.get("/hotspots/{hotspot_id}/thumbnail")
def get_hotspot_thumbnail(hotspot_id: uuid.UUID, service: HotspotServiceDep) -> Response:
    return Response(service.get_thumbnail(hotspot_id), media_type=MEDIA_TYPE_JPEG, headers=IMMUTABLE_CACHE_HEADERS)


@router.get("/hotspots/{hotspot_id}/annotations", response_model=list[AnnotationRead])
def list_hotspot_annotations(hotspot_id: uuid.UUID, service: HotspotAnnotationServiceDep) -> list[AnnotationRead]:
    return service.list_annotations(hotspot_id)


@router.get("/hotspots/{hotspot_id}/annotations/export")
def export_hotspot_annotations(hotspot_id: uuid.UUID, service: HotspotAnnotationServiceDep) -> JSONResponse:
    filename = HOTSPOT_ANNOTATIONS_EXPORT_FILENAME.format(hotspot_id=hotspot_id)
    return JSONResponse(
        service.export_geojson(hotspot_id).model_dump(mode="json"),
        media_type=MEDIA_TYPE_GEOJSON,
        headers={"Content-Disposition": CONTENT_DISPOSITION_ATTACHMENT.format(filename=filename)},
    )


@router.post(
    "/hotspots/{hotspot_id}/annotations", response_model=AnnotationRead, status_code=status.HTTP_201_CREATED
)
def create_hotspot_annotation(
    hotspot_id: uuid.UUID, data: AnnotationCreate, service: HotspotAnnotationServiceDep, user: CurrentUserDep
) -> AnnotationRead:
    return service.create_annotation(hotspot_id, data, user)


@router.patch("/hotspots/{hotspot_id}/annotations/{annotation_id}", response_model=AnnotationRead)
def update_hotspot_annotation(
    hotspot_id: uuid.UUID, annotation_id: uuid.UUID, data: AnnotationUpdate, service: HotspotAnnotationServiceDep
) -> AnnotationRead:
    return service.update_annotation(hotspot_id, annotation_id, data)


@router.delete("/hotspots/{hotspot_id}/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_hotspot_annotation(
    hotspot_id: uuid.UUID, annotation_id: uuid.UUID, service: HotspotAnnotationServiceDep
) -> None:
    service.delete_annotation(hotspot_id, annotation_id)
