import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.core.constants import CONTENT_DISPOSITION_ATTACHMENT, MEDIA_TYPE_XML
from app.core.http_cache import etag_json_response, parse_if_match
from app.dependencies.auth import get_current_user, require_admin
from app.dependencies.services import SlideServiceDep
from app.schemas.slides import RegionQuery, SlideDetail, SlideRead, SlideRenameRequest
from app.services.slide_analysis import MEDIA_TYPE_JPEG, MEDIA_TYPE_PNG

REGION_FILENAME = "region_{x}_{y}_{width}x{height}.png"

router = APIRouter()

# Image endpoints (DZI, tiles, thumbnail) stay public: OpenSeadragon and <img> load them as plain
# cross-origin images, without the session cookie.
IfMatch = Annotated[str | None, Header()]


# Every client polls these: an unchanged list costs a 304 with no body (see `etag_json_response`).
@router.get("", response_model=list[SlideRead], dependencies=[Depends(get_current_user)])
def list_slides(request: Request, service: SlideServiceDep) -> Response:
    return etag_json_response(request, [slide.model_dump(mode="json") for slide in service.list_slides()])


@router.get("/{slide_id}", response_model=SlideDetail, dependencies=[Depends(get_current_user)])
def get_slide(slide_id: uuid.UUID, request: Request, service: SlideServiceDep) -> Response:
    return etag_json_response(request, service.get_slide(slide_id).model_dump(mode="json"))


@router.patch("/{slide_id}", response_model=SlideRead, dependencies=[Depends(require_admin)])
def rename_slide(
    slide_id: uuid.UUID, req: SlideRenameRequest, service: SlideServiceDep, if_match: IfMatch = None
) -> SlideRead:
    return service.rename_slide(slide_id, req.name, parse_if_match(if_match))


@router.delete("/{slide_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
def delete_slide(slide_id: uuid.UUID, service: SlideServiceDep, if_match: IfMatch = None) -> None:
    service.delete_slide(slide_id, parse_if_match(if_match))


@router.get("/{slide_id}/image.dzi")
def get_dzi(slide_id: uuid.UUID, service: SlideServiceDep) -> Response:
    # Slide content never changes for a given id, so this can be cached forever.
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    return Response(service.get_dzi(slide_id), media_type=MEDIA_TYPE_XML, headers=headers)


# Standard Deep Zoom layout: OpenSeadragon derives "<name>_files/" from the "<name>.dzi" URL.
@router.get("/{slide_id}/image_files/{level}/{col}_{row}.jpeg")
def get_tile(slide_id: uuid.UUID, level: int, col: int, row: int, service: SlideServiceDep) -> Response:
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    return Response(service.get_tile(slide_id, level, col, row), media_type=MEDIA_TYPE_JPEG, headers=headers)


@router.get("/{slide_id}/thumbnail")
def get_thumbnail(slide_id: uuid.UUID, service: SlideServiceDep) -> Response:
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    return Response(service.get_thumbnail(slide_id), media_type=MEDIA_TYPE_JPEG, headers=headers)


@router.get("/{slide_id}/region")
def export_region(slide_id: uuid.UUID, query: Annotated[RegionQuery, Query()], service: SlideServiceDep) -> Response:
    filename = REGION_FILENAME.format(x=query.x, y=query.y, width=query.width, height=query.height)
    return Response(
        service.export_region(slide_id, query),
        media_type=MEDIA_TYPE_PNG,
        headers={"Content-Disposition": CONTENT_DISPOSITION_ATTACHMENT.format(filename=filename)},
    )
