import uuid
from typing import Any

from pydantic import ValidationError

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models.annotation import Annotation
from app.models.slide import Slide
from app.repositories.annotations import AnnotationRepository
from app.repositories.slides import SlideRepository
from app.schemas.annotations import (
    AnnotationClassification,
    AnnotationClassRead,
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
ANNOTATION_CLASS_NOT_FOUND = "Annotation class {class_id} does not exist."
POLYGON_OUTSIDE_SLIDE = "The polygon lies outside the slide."


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _fit_to_slide(geometry: PolygonGeometry, slide: Slide) -> dict[str, Any]:
    """Clamp vertices to the slide area (shapes may be drawn past the image edge)."""
    width, height = float(slide.width or 0), float(slide.height or 0)
    ring = [(min(max(x, 0.0), width), min(max(y, 0.0), height)) for x, y in geometry.coordinates[0]]
    try:
        return PolygonGeometry(coordinates=[ring]).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidInputError(POLYGON_OUTSIDE_SLIDE) from exc


class AnnotationService:
    """Tissue classes and the polygon annotations of a slide (always the slide in the request path)."""

    def __init__(self, annotations: AnnotationRepository, slides: SlideRepository) -> None:
        self._annotations = annotations
        self._slides = slides

    def list_classes(self) -> list[AnnotationClassRead]:
        return [AnnotationClassRead.model_validate(item) for item in self._annotations.list_classes()]

    def list_annotations(self, slide_id: uuid.UUID) -> list[AnnotationRead]:
        require_slide(self._slides, slide_id)
        return [AnnotationRead.model_validate(item) for item in self._annotations.list_for_slide(slide_id)]

    def create_annotation(self, slide_id: uuid.UUID, data: AnnotationCreate) -> AnnotationRead:
        slide = require_slide(self._slides, slide_id, ready=True)
        self._require_class(data.class_id)
        annotation_id = data.id or uuid.uuid4()
        if self._annotations.get(annotation_id) is not None:
            raise ConflictError(ANNOTATION_ALREADY_EXISTS.format(annotation_id=annotation_id))
        annotation = Annotation(
            id=annotation_id, slide_id=slide_id, class_id=data.class_id, geometry=_fit_to_slide(data.geometry, slide)
        )
        return AnnotationRead.model_validate(self._annotations.save(annotation))

    def update_annotation(
        self, slide_id: uuid.UUID, annotation_id: uuid.UUID, data: AnnotationUpdate
    ) -> AnnotationRead:
        slide = require_slide(self._slides, slide_id, ready=True)
        annotation = self._require_annotation(slide_id, annotation_id)
        if data.class_id is not None:
            self._require_class(data.class_id)
            annotation.class_id = data.class_id
        if data.geometry is not None:
            annotation.geometry = _fit_to_slide(data.geometry, slide)
        return AnnotationRead.model_validate(self._annotations.save(annotation))

    def delete_annotation(self, slide_id: uuid.UUID, annotation_id: uuid.UUID) -> None:
        self._annotations.delete(self._require_annotation(slide_id, annotation_id))

    def export_geojson(self, slide_id: uuid.UUID) -> AnnotationFeatureCollection:
        require_slide(self._slides, slide_id)
        classes = {item.id: item for item in self._annotations.list_classes()}
        return AnnotationFeatureCollection(
            features=[
                AnnotationFeature(
                    id=annotation.id,
                    geometry=PolygonGeometry.model_validate(annotation.geometry),
                    properties=AnnotationFeatureProperties(
                        classification=AnnotationClassification(
                            name=classes[annotation.class_id].name,
                            color=_hex_to_rgb(classes[annotation.class_id].color),
                        )
                    ),
                )
                for annotation in self._annotations.list_for_slide(slide_id)
            ]
        )

    def _require_class(self, class_id: uuid.UUID) -> None:
        if self._annotations.get_class(class_id) is None:
            raise InvalidInputError(ANNOTATION_CLASS_NOT_FOUND.format(class_id=class_id))

    def _require_annotation(self, slide_id: uuid.UUID, annotation_id: uuid.UUID) -> Annotation:
        annotation = self._annotations.get(annotation_id)
        if annotation is None or annotation.slide_id != slide_id:
            raise NotFoundError(ANNOTATION_NOT_FOUND.format(annotation_id=annotation_id))
        return annotation
