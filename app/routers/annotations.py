import uuid

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.constants import CONTENT_DISPOSITION_ATTACHMENT, MEDIA_TYPE_GEOJSON
from app.dependencies.services import AnnotationServiceDep, CellTypeServiceDep
from app.schemas.annotations import AnnotationClassRead, AnnotationCreate, AnnotationRead, AnnotationUpdate

ANNOTATIONS_EXPORT_FILENAME = "annotations_{slide_id}.geojson"

router = APIRouter()


@router.get("/annotation-classes", response_model=list[AnnotationClassRead])
def list_annotation_classes(cell_types: CellTypeServiceDep) -> list[AnnotationClassRead]:
    return [
        AnnotationClassRead(id=str(item.id), name=item.name, color="#3b82f6")
        for item in cell_types.list_all()
    ]


@router.get("/slides/{slide_id}/annotations", response_model=list[AnnotationRead])
def list_annotations(slide_id: uuid.UUID, service: AnnotationServiceDep) -> list[AnnotationRead]:
    return service.list_annotations(slide_id)


@router.get("/slides/{slide_id}/annotations/export")
def export_annotations(slide_id: uuid.UUID, service: AnnotationServiceDep) -> JSONResponse:
    filename = ANNOTATIONS_EXPORT_FILENAME.format(slide_id=slide_id)
    return JSONResponse(
        service.export_geojson(slide_id).model_dump(mode="json"),
        media_type=MEDIA_TYPE_GEOJSON,
        headers={"Content-Disposition": CONTENT_DISPOSITION_ATTACHMENT.format(filename=filename)},
    )


@router.post("/slides/{slide_id}/annotations", response_model=AnnotationRead, status_code=status.HTTP_201_CREATED)
def create_annotation(slide_id: uuid.UUID, data: AnnotationCreate, service: AnnotationServiceDep) -> AnnotationRead:
    return service.create_annotation(slide_id, data)


@router.patch("/slides/{slide_id}/annotations/{annotation_id}", response_model=AnnotationRead)
def update_annotation(
    slide_id: uuid.UUID, annotation_id: uuid.UUID, data: AnnotationUpdate, service: AnnotationServiceDep
) -> AnnotationRead:
    return service.update_annotation(slide_id, annotation_id, data)


@router.delete("/slides/{slide_id}/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annotation(slide_id: uuid.UUID, annotation_id: uuid.UUID, service: AnnotationServiceDep) -> None:
    service.delete_annotation(slide_id, annotation_id)
