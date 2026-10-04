import uuid
from typing import Any

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models.hotspot import Hotspot, HotspotAnnotation
from app.repositories.annotations import AnnotationRepository
from app.repositories.hotspots import HotspotRepository
from app.schemas.annotations import (
    AnnotationCreate,
    AnnotationFeatureCollection,
    AnnotationRead,
    AnnotationUpdate,
    PolygonGeometry,
)
from app.services.annotations import (
    ANNOTATION_ALREADY_EXISTS,
    ANNOTATION_CLASS_NOT_FOUND,
    build_feature_collection,
    fit_to_bounds,
)
from app.services.hotspots import require_hotspot

HOTSPOT_ANNOTATION_NOT_FOUND = "Annotation {annotation_id} does not exist on this hotspot."


class HotspotAnnotationService:
    """Polygon annotations of a hotspot, in hotspot image pixels (same classes as slide annotations)."""

    def __init__(self, hotspots: HotspotRepository, annotations: AnnotationRepository) -> None:
        self._hotspots = hotspots
        # Only for the shared tissue classes.
        self._classes = annotations

    def list_annotations(self, hotspot_id: uuid.UUID) -> list[AnnotationRead]:
        require_hotspot(self._hotspots, hotspot_id)
        return [AnnotationRead.model_validate(item) for item in self._hotspots.list_annotations(hotspot_id)]

    def create_annotation(self, hotspot_id: uuid.UUID, data: AnnotationCreate) -> AnnotationRead:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        self._require_class(data.class_id)
        annotation_id = data.id or uuid.uuid4()
        if self._hotspots.get_annotation(annotation_id) is not None:
            raise ConflictError(ANNOTATION_ALREADY_EXISTS.format(annotation_id=annotation_id))
        annotation = HotspotAnnotation(
            id=annotation_id, hotspot_id=hotspot_id, class_id=data.class_id, geometry=_fit(data.geometry, hotspot)
        )
        return AnnotationRead.model_validate(self._hotspots.save(annotation))

    def update_annotation(
        self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID, data: AnnotationUpdate
    ) -> AnnotationRead:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        annotation = self._require_annotation(hotspot_id, annotation_id)
        if data.class_id is not None:
            self._require_class(data.class_id)
            annotation.class_id = data.class_id
        if data.geometry is not None:
            annotation.geometry = _fit(data.geometry, hotspot)
        return AnnotationRead.model_validate(self._hotspots.save(annotation))

    def delete_annotation(self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID) -> None:
        self._hotspots.delete(self._require_annotation(hotspot_id, annotation_id))

    def export_geojson(self, hotspot_id: uuid.UUID) -> AnnotationFeatureCollection:
        require_hotspot(self._hotspots, hotspot_id)
        return build_feature_collection(self._hotspots.list_annotations(hotspot_id), self._classes.list_classes())

    def _require_class(self, class_id: uuid.UUID) -> None:
        if self._classes.get_class(class_id) is None:
            raise InvalidInputError(ANNOTATION_CLASS_NOT_FOUND.format(class_id=class_id))

    def _require_annotation(self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID) -> HotspotAnnotation:
        annotation = self._hotspots.get_annotation(annotation_id)
        if annotation is None or annotation.hotspot_id != hotspot_id:
            raise NotFoundError(HOTSPOT_ANNOTATION_NOT_FOUND.format(annotation_id=annotation_id))
        return annotation


def _fit(geometry: PolygonGeometry, hotspot: Hotspot) -> dict[str, Any]:
    """Clamp vertices to the hotspot image."""
    return fit_to_bounds(geometry, float(hotspot.image_width), float(hotspot.image_height))
