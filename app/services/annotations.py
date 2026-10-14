import uuid
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models.annotation import Annotation
from app.models.cell_type import CellType
from app.models.slide import Slide
from app.repositories.annotations import AnnotationRepository
from app.repositories.cell_types import CellTypeRepository
from app.repositories.slides import SlideRepository
from app.schemas.annotations import (
    AnnotationClassification,
    AnnotationCreate,
    AnnotationFeature,
    AnnotationFeatureCollection,
    AnnotationFeatureProperties,
    AnnotationRead,
    AnnotationUpdate,
    PolygonGeometry,
)
from app.services.slides import require_slide

ANNOTATION_NOT_FOUND = "Annotation {annotation_id} does not exist on this slide."
ANNOTATION_ALREADY_EXISTS = "Annotation {annotation_id} already exists."
CELL_TYPE_NOT_FOUND = "Cell type {cell_type_id} does not exist."
POLYGON_OUTSIDE_IMAGE = "The polygon lies outside the image."


def fit_to_bounds(geometry: PolygonGeometry, width: float, height: float) -> dict[str, Any]:
    """Clamp vertices to the image area [0, width] x [0, height] (shapes may be drawn past its edge)."""
    ring = [(min(max(x, 0.0), width), min(max(y, 0.0), height)) for x, y in geometry.coordinates[0]]
    try:
        return PolygonGeometry(coordinates=[ring]).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidInputError(POLYGON_OUTSIDE_IMAGE) from exc


def _fit_to_slide(geometry: PolygonGeometry, slide: Slide) -> dict[str, Any]:
    return fit_to_bounds(geometry, float(slide.width or 0), float(slide.height or 0))


def build_feature_collection(
    annotations: Iterable[Annotation], cell_types: Iterable[CellType]
) -> AnnotationFeatureCollection:
    """QuPath-compatible GeoJSON of the given annotations (coordinates as stored)."""
    by_id = {item.id: item for item in cell_types}
    features = []
    for annotation in annotations:
        ct = by_id.get(annotation.cell_type_id) if annotation.cell_type_id is not None else None
        name = ct.name if ct else "Unclassified"
        # Deterministic RGB from cell_type_id
        color = (
            ((annotation.cell_type_id * 67) % 256, (annotation.cell_type_id * 131) % 256, (annotation.cell_type_id * 197) % 256)
            if annotation.cell_type_id is not None
            else (128, 128, 128)
        )
        features.append(
            AnnotationFeature(
                id=annotation.id,
                geometry=PolygonGeometry.model_validate(annotation.geometry),
                properties=AnnotationFeatureProperties(
                    classification=AnnotationClassification(
                        name=name,
                        color=color,
                    )
                ),
            )
        )
    return AnnotationFeatureCollection(features=features)


class AnnotationService:
    """Polygon annotations of a slide (always the slide in the request path)."""

    def __init__(
        self,
        annotations: AnnotationRepository,
        slides: SlideRepository,
        cell_types: CellTypeRepository,
    ) -> None:
        self._annotations = annotations
        self._slides = slides
        self._cell_types = cell_types

    def list_annotations(self, slide_id: uuid.UUID) -> list[AnnotationRead]:
        require_slide(self._slides, slide_id)
        return [AnnotationRead.model_validate(item) for item in self._annotations.list_for_slide(slide_id)]

    def create_annotation(self, slide_id: uuid.UUID, data: AnnotationCreate) -> AnnotationRead:
        slide = require_slide(self._slides, slide_id, ready=True)
        assert data.cell_type_id is not None
        self._require_cell_type(data.cell_type_id)
        annotation_id = data.id or uuid.uuid4()
        if self._annotations.get(annotation_id) is not None:
            raise ConflictError(ANNOTATION_ALREADY_EXISTS.format(annotation_id=annotation_id))
        annotation = Annotation(
            id=annotation_id,
            slide_id=slide_id,
            cell_type_id=data.cell_type_id,
            geometry=_fit_to_slide(data.geometry, slide),
        )
        return AnnotationRead.model_validate(self._annotations.save(annotation))

    def update_annotation(
        self, slide_id: uuid.UUID, annotation_id: uuid.UUID, data: AnnotationUpdate
    ) -> AnnotationRead:
        slide = require_slide(self._slides, slide_id, ready=True)
        annotation = self._require_annotation(slide_id, annotation_id)
        if data.cell_type_id is not None:
            self._require_cell_type(data.cell_type_id)
            annotation.cell_type_id = data.cell_type_id
        if data.geometry is not None:
            annotation.geometry = _fit_to_slide(data.geometry, slide)
        return AnnotationRead.model_validate(self._annotations.save(annotation))

    def delete_annotation(self, slide_id: uuid.UUID, annotation_id: uuid.UUID) -> None:
        self._annotations.delete(self._require_annotation(slide_id, annotation_id))

    def export_geojson(self, slide_id: uuid.UUID) -> AnnotationFeatureCollection:
        require_slide(self._slides, slide_id)
        return build_feature_collection(
            self._annotations.list_for_slide(slide_id),
            self._cell_types.list_all(),
        )

    def _require_cell_type(self, cell_type_id: int) -> None:
        if self._cell_types.get(cell_type_id) is None:
            raise InvalidInputError(CELL_TYPE_NOT_FOUND.format(cell_type_id=cell_type_id))

    def _require_annotation(self, slide_id: uuid.UUID, annotation_id: uuid.UUID) -> Annotation:
        annotation = self._annotations.get(annotation_id)
        if annotation is None or annotation.slide_id != slide_id:
            raise NotFoundError(ANNOTATION_NOT_FOUND.format(annotation_id=annotation_id))
        return annotation
