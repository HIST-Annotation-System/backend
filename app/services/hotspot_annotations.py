import uuid
from typing import Any

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models.account import Account
from app.models.hotspot import AnnotationKind, Hotspot, HotspotAnnotation
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
    cells_inside_excluded,
    excluded_areas_overlap,
    fit_excluded_area,
    fit_shape_to_bounds,
    merge_excluded_areas,
)
from app.services.hotspots import require_hotspot

HOTSPOT_ANNOTATION_NOT_FOUND = "Annotation {annotation_id} does not exist on this hotspot."
EXCLUDED_AREA_HAS_NO_CELL_TYPE = "An excluded area has no cell type; switch it to a cell first."


class HotspotAnnotationService:
    """Polygon annotations of a hotspot, in hotspot image pixels, optionally drawn as Bézier curves.

    An annotation is a cell (with a cell type) or an area excluded from model training (without one).
    """

    def __init__(self, hotspots: HotspotRepository, cell_types: CellTypeRepository) -> None:
        self._hotspots = hotspots
        self._cell_types = cell_types

    def list_annotations(self, hotspot_id: uuid.UUID) -> list[AnnotationRead]:
        require_hotspot(self._hotspots, hotspot_id)
        annotations = self._hotspots.list_annotations(hotspot_id)
        inside = cells_inside_excluded(annotations)
        return [_read(item, item.id in inside) for item in annotations]

    def create_annotation(self, hotspot_id: uuid.UUID, data: AnnotationCreate, author: Account) -> AnnotationRead:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        if data.cell_type_id is not None:
            self._require_cell_type(data.cell_type_id)
        annotation_id = data.id or uuid.uuid4()
        if self._hotspots.get_annotation(annotation_id) is not None:
            raise ConflictError(ANNOTATION_ALREADY_EXISTS.format(annotation_id=annotation_id))
        geometry, curve = _fit(data.geometry, data.curve, hotspot)
        if data.kind == AnnotationKind.EXCLUDED:
            geometry, repaired = _fit_area(geometry, hotspot)
            if repaired:
                # The repaired outline no longer follows the drawn curve: it is stored as a plain polygon.
                curve = None
        annotation = HotspotAnnotation(
            id=annotation_id,
            hotspot_id=hotspot_id,
            kind=data.kind,
            cell_type_id=data.cell_type_id,
            geometry=geometry,
            curve=curve,
            created_by_id=author.id,
        )
        if annotation.kind == AnnotationKind.EXCLUDED:
            return self._save_area(annotation, hotspot)
        return self._read_saved(self._hotspots.save(annotation))

    def update_annotation(
        self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID, data: AnnotationUpdate
    ) -> AnnotationRead:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        annotation = self._require_annotation(hotspot_id, annotation_id)
        kind = data.kind or annotation.kind
        if data.cell_type_id is not None:
            if kind == AnnotationKind.EXCLUDED:
                raise InvalidInputError(EXCLUDED_AREA_HAS_NO_CELL_TYPE)
            self._require_cell_type(data.cell_type_id)
            annotation.cell_type_id = data.cell_type_id
        annotation.kind = kind
        if kind == AnnotationKind.EXCLUDED:
            annotation.cell_type_id = None
        if data.curve is not None or data.geometry is not None:
            # New vertices without a curve leave no curve: a stored one would no longer match them.
            annotation.geometry, annotation.curve = _fit(data.geometry, data.curve, hotspot)
        elif "curve" in data.model_fields_set:
            annotation.curve = None
        if kind == AnnotationKind.EXCLUDED:
            annotation.geometry, repaired = _fit_area(annotation.geometry, hotspot)
            if repaired:
                annotation.curve = None
            return self._save_area(annotation, hotspot)
        return self._read_saved(self._hotspots.save(annotation))

    def delete_annotation(self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID) -> None:
        self._hotspots.delete(self._require_annotation(hotspot_id, annotation_id))

    def export_geojson(self, hotspot_id: uuid.UUID) -> AnnotationFeatureCollection:
        require_hotspot(self._hotspots, hotspot_id)
        return build_feature_collection(
            self._hotspots.list_annotations(hotspot_id),
            self._cell_types.list_all(),
        )

    def _save_area(self, area: HotspotAnnotation, hotspot: Hotspot) -> AnnotationRead:
        """
        Saves an excluded area merged with the author's other areas on the hotspot that it crosses or touches,
        directly or through each other, so overlapping areas become one shape. The oldest of them keeps its id
        (the drawn one may be brand new); the rest are deleted and listed in `absorbed_ids`.
        """
        group = self._overlapping_areas(area)
        if not group:
            return self._read_saved(self._hotspots.save(area))
        # A new area has no created_at yet and is newer than every stored one; on a tie the earlier listed wins.
        stored = [*group, *([area] if area.created_at is not None else [])]
        kept = min(stored, key=lambda item: item.created_at)
        kept.geometry = merge_excluded_areas(
            [area.geometry, *(item.geometry for item in group)],
            float(hotspot.image_width),
            float(hotspot.image_height),
        )
        kept.curve = None
        absorbed = [item for item in [area, *group] if item is not kept]
        saved = self._hotspots.save_merged(kept, [item for item in absorbed if item.created_at is not None])
        read = self._read_saved(saved)
        read.absorbed_ids = [item.id for item in absorbed]
        return read

    def _overlapping_areas(self, area: HotspotAnnotation) -> list[HotspotAnnotation]:
        """The author's other excluded areas on the hotspot reached from `area` by crossing or touching."""
        if area.created_by_id is None:
            return []
        others = [
            item
            for item in self._hotspots.list_annotations(area.hotspot_id)
            if item.kind == AnnotationKind.EXCLUDED and item.id != area.id and item.created_by_id == area.created_by_id
        ]
        group: list[HotspotAnnotation] = []
        shapes = [area.geometry]
        while True:
            reached = [
                item
                for item in others
                if item not in group and any(excluded_areas_overlap(item.geometry, shape) for shape in shapes)
            ]
            if not reached:
                return group
            group.extend(reached)
            shapes.extend(item.geometry for item in reached)

    def _read_saved(self, annotation: HotspotAnnotation) -> AnnotationRead:
        """The annotation as listed, with its warning against the hotspot's current excluded areas."""
        if annotation.kind == AnnotationKind.EXCLUDED:
            return _read(annotation, False)
        areas = [
            item
            for item in self._hotspots.list_annotations(annotation.hotspot_id)
            if item.kind == AnnotationKind.EXCLUDED
        ]
        return _read(annotation, annotation.id in cells_inside_excluded([*areas, annotation]))

    def _require_cell_type(self, cell_type_id: int) -> None:
        if self._cell_types.get(cell_type_id) is None:
            raise InvalidInputError(CELL_TYPE_NOT_FOUND.format(cell_type_id=cell_type_id))

    def _require_annotation(self, hotspot_id: uuid.UUID, annotation_id: uuid.UUID) -> HotspotAnnotation:
        annotation = self._hotspots.get_annotation(annotation_id)
        if annotation is None or annotation.hotspot_id != hotspot_id:
            raise NotFoundError(HOTSPOT_ANNOTATION_NOT_FOUND.format(annotation_id=annotation_id))
        return annotation


def _read(annotation: HotspotAnnotation, inside_excluded: bool) -> AnnotationRead:
    read = AnnotationRead.model_validate(annotation)
    read.inside_excluded = inside_excluded
    return read


def _fit_area(geometry: dict[str, Any], hotspot: Hotspot) -> tuple[dict[str, Any], bool]:
    """An excluded area as stored: valid as drawn, or repaired (see `fit_excluded_area`)."""
    return fit_excluded_area(geometry, float(hotspot.image_width), float(hotspot.image_height))


def _fit(
    geometry: PolygonGeometry | None, curve: CurveGeometry | None, hotspot: Hotspot
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Clamp the shape to the hotspot image: the (geometry, curve) pair to store."""
    return fit_shape_to_bounds(geometry, curve, float(hotspot.image_width), float(hotspot.image_height))
