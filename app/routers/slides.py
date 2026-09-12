import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.dependencies.services import SlideServiceDep
from app.schemas.slides import RegionQuery, SlideDetail, SlideRead
from app.services.slide_analysis import MEDIA_TYPE_JPEG, MEDIA_TYPE_PNG

MEDIA_TYPE_XML = "application/xml"
# Slide content never changes for a given id, so tiles and derived images can be cached forever.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
CONTENT_DISPOSITION_ATTACHMENT = 'attachment; filename="{filename}"'
REGION_FILENAME = "region_{x}_{y}_{width}x{height}.png"

router = APIRouter()


@router.get("", response_model=list[SlideRead])
def list_slides(service: SlideServiceDep) -> list[SlideRead]:
    return service.list_slides()


@router.get("/{slide_id}", response_model=SlideDetail)
def get_slide(slide_id: uuid.UUID, service: SlideServiceDep) -> SlideDetail:
    return service.get_slide(slide_id)


@router.get("/{slide_id}/image.dzi")
def get_dzi(slide_id: uuid.UUID, service: SlideServiceDep) -> Response:
    return Response(service.get_dzi(slide_id), media_type=MEDIA_TYPE_XML, headers=IMMUTABLE)


# Standard Deep Zoom layout: OpenSeadragon derives "<name>_files/" from the "<name>.dzi" URL.
@router.get("/{slide_id}/image_files/{level}/{col}_{row}.jpeg")
def get_tile(slide_id: uuid.UUID, level: int, col: int, row: int, service: SlideServiceDep) -> Response:
    return Response(service.get_tile(slide_id, level, col, row), media_type=MEDIA_TYPE_JPEG, headers=IMMUTABLE)


@router.get("/{slide_id}/thumbnail")
def get_thumbnail(slide_id: uuid.UUID, service: SlideServiceDep) -> Response:
    return Response(service.get_thumbnail(slide_id), media_type=MEDIA_TYPE_JPEG, headers=IMMUTABLE)


@router.get("/{slide_id}/region")
def export_region(slide_id: uuid.UUID, query: Annotated[RegionQuery, Query()], service: SlideServiceDep) -> Response:
    filename = REGION_FILENAME.format(x=query.x, y=query.y, width=query.width, height=query.height)
    return Response(
        service.export_region(slide_id, query),
        media_type=MEDIA_TYPE_PNG,
        headers={"Content-Disposition": CONTENT_DISPOSITION_ATTACHMENT.format(filename=filename)},
    )
