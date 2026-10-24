import uuid
from typing import Any

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models.account import Account
from app.models.hotspot import Hotspot, HotspotAnnotation
from app.repositories.cell_types import CellTypeRepository
from app.repositories.hotspots import HotspotRepository
from app.schemas.annotations import (
    AnnotationCreate,
    AnnotationFeatureCollection,
    AnnotationRead,
    AnnotationUpdate,
    CurveGeometry,
    PolygonGeometry,
)
from app.services.annotations import (
    ANNOTATION_ALREADY_EXISTS,
    CELL_TYPE_NOT_FOUND,
    build_feature_collection,
    fit_shape_to_bounds,
)
from app.services.hotspots import require_hotspot

HOTSPOT_ANNOTATION_NOT_FOUND = "Annotation {annotation_id} does not exist on this hotspot."


class HotspotAnnotationService:
    """Polygon annotations of a hotspot, in hotspot image pixels, optionally drawn as Bézier curves."""

    def __init__(self, hotspots: HotspotRepository, cell_types: CellTypeRepository) -> None:
        self._hotspots = hotspots
        self._cell_types = cell_types

    def list_annotations(self, hotspot_id: uuid.UUID) -> list[AnnotationRead]:
        require_hotspot(self._hotspots, hotspot_id)
        return [AnnotationRead.model_validate(item) for item in self._hotspots.list_annotations(hotspot_id)]

    def create_annotation(self, hotspot_id: uuid.UUID, data: AnnotationCreate, author: Account) -> AnnotationRead:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        assert data.cell_type_id is not None
        self._require_cell_type(data.cell_type_id)
        annotation_id = data.id or uuid.uuid4()
        if self._hotspots.get_annotation(annotation_id) is not None:
            raise ConflictError(ANNOTATION_ALREADY_EXISTS.format(annotation_id=annotation_id))
        geometry, curve = _fit(data.geometry, data.curve, hotspot)
        annotation = HotspotAnnotation(
            id=annotation_id,
            hotspot_id=hotspot_id,
            cell_type_id=data.cell_type_id,
            geometry=geometry,
            curve=curve,
            created_by_id=author.id,
        )
        return AnnotationRead.model_validate(self._hotspots.save(annotation))

    def update_annotation(
        self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID, data: AnnotationUpdate
    ) -> AnnotationRead:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        annotation = self._require_annotation(hotspot_id, annotation_id)
        if data.cell_type_id is not None:
            self._require_cell_type(data.cell_type_id)
            annotation.cell_type_id = data.cell_type_id
        if data.curve is not None or data.geometry is not None:
            # New vertices without a curve leave no curve: a stored one would no longer match them.
            annotation.geometry, annotation.curve = _fit(data.geometry, data.curve, hotspot)
        elif "curve" in data.model_fields_set:
            annotation.curve = None
        return AnnotationRead.model_validate(self._hotspots.save(annotation))

    def delete_annotation(self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID) -> None:
        self._hotspots.delete(self._require_annotation(hotspot_id, annotation_id))

    def export_geojson(self, hotspot_id: uuid.UUID) -> AnnotationFeatureCollection:
        require_hotspot(self._hotspots, hotspot_id)
        return build_feature_collection(
            self._hotspots.list_annotations(hotspot_id),
            self._cell_types.list_all(),
        )

    def _require_cell_type(self, cell_type_id: int) -> None:
        if self._cell_types.get(cell_type_id) is None:
            raise InvalidInputError(CELL_TYPE_NOT_FOUND.format(cell_type_id=cell_type_id))

    def _require_annotation(self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID) -> HotspotAnnotation:
        annotation = self._hotspots.get_annotation(annotation_id)
        if annotation is None or annotation.hotspot_id != hotspot_id:
            raise NotFoundError(HOTSPOT_ANNOTATION_NOT_FOUND.format(annotation_id=annotation_id))
        return annotation


def _fit(
    geometry: PolygonGeometry | None, curve: CurveGeometry | None, hotspot: Hotspot
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Clamp the shape to the hotspot image: the (geometry, curve) pair to store."""
    return fit_shape_to_bounds(geometry, curve, float(hotspot.image_width), float(hotspot.image_height))
