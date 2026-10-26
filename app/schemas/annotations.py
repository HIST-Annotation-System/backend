import math
import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

from app.models.hotspot import AnnotationKind

ANNOTATION_MAX_VERTICES = 10_000
POLYGON_SINGLE_RING = "A polygon must have exactly one exterior ring (holes are not supported)."
POLYGON_NOT_FINITE = "Coordinates must be finite numbers."
POLYGON_TOO_FEW_VERTICES = "A polygon needs at least 3 distinct vertices."
POLYGON_TOO_MANY_VERTICES = f"A polygon may have at most {ANNOTATION_MAX_VERTICES} vertices."
ANNOTATION_UPDATE_EMPTY = "Provide kind, cell_type_id, geometry and/or curve."
CELL_TYPE_REQUIRED = "cell_type_id is required"
EXCLUDED_WITH_CELL_TYPE = "An excluded area has no cell type."
CURVE_MIN_POINTS = 3
# Polygon vertices per curved segment of a flattened curve; must match the frontend.
CURVE_SEGMENT_STEPS = 16
CURVE_TOO_FEW_POINTS = f"A curve needs at least {CURVE_MIN_POINTS} points."
CURVE_TOO_MANY_VERTICES = f"A curve may flatten to at most {ANNOTATION_MAX_VERTICES} polygon vertices."
CURVE_NOT_FINITE = "Curve coordinates must be finite numbers."

Position = tuple[float, float]


class PolygonGeometry(BaseModel):
    """GeoJSON Polygon in level-0 slide pixels: one exterior ring, closed automatically."""

    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[Position]]

    @field_validator("coordinates")
    @classmethod
    def _normalize_ring(cls, rings: list[list[Position]]) -> list[list[Position]]:
        if len(rings) != 1:
            raise ValueError(POLYGON_SINGLE_RING)
        if not all(math.isfinite(value) for point in rings[0] for value in point):
            raise ValueError(POLYGON_NOT_FINITE)
        # Repeated consecutive vertices carry no shape information.
        ring = [point for index, point in enumerate(rings[0]) if index == 0 or point != rings[0][index - 1]]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(set(ring)) < 3:
            raise ValueError(POLYGON_TOO_FEW_VERTICES)
        if len(ring) - 1 > ANNOTATION_MAX_VERTICES:
            raise ValueError(POLYGON_TOO_MANY_VERTICES)
        return [ring]


class CurvePoint(BaseModel):
    """A corner, or a smooth point with two cubic Bézier handles."""

    type: Literal["corner", "curve"]
    point: Position
    in_handle: Position | None = None
    out_handle: Position | None = None
    locked: bool = False

    @field_validator("point", "in_handle", "out_handle")
    @classmethod
    def _require_finite(cls, position: Position | None) -> Position | None:
        if position is not None and not all(math.isfinite(value) for value in position):
            raise ValueError(CURVE_NOT_FINITE)
        return position

    @model_validator(mode="after")
    def _drop_corner_handles(self) -> Self:
        if self.type == "corner":
            self.in_handle = None
            self.out_handle = None
        return self


class CurveGeometry(BaseModel):
    """Closed Bézier path in the same pixels as the polygon, which is derived from it."""

    points: list[CurvePoint]

    @field_validator("points")
    @classmethod
    def _check_point_count(cls, points: list[CurvePoint]) -> list[CurvePoint]:
        if len(points) < CURVE_MIN_POINTS:
            raise ValueError(CURVE_TOO_FEW_POINTS)
        curved_segments = sum(
            1
            for index, point in enumerate(points)
            if point.type == "curve" or points[(index + 1) % len(points)].type == "curve"
        )
        if len(points) + (CURVE_SEGMENT_STEPS - 1) * curved_segments > ANNOTATION_MAX_VERTICES:
            raise ValueError(CURVE_TOO_MANY_VERTICES)
        return points


def _resolve_class_id(cell_type_id: int | None, class_id: int | str | None) -> int | None:
    if cell_type_id is None and class_id is not None:
        try:
            return int(class_id)
        except ValueError:
            raise ValueError("class_id must be convertible to int")
    return cell_type_id


class AnnotationCreate(BaseModel):
    # The annotator's own UUID (keeps its undo/redo history consistent); generated when omitted.
    id: uuid.UUID | None = None
    kind: AnnotationKind = AnnotationKind.CELL
    cell_type_id: int | None = None
    class_id: int | str | None = None
    geometry: PolygonGeometry
    curve: CurveGeometry | None = None

    @model_validator(mode="after")
    def _resolve_cell_type(self) -> Self:
        self.cell_type_id = _resolve_class_id(self.cell_type_id, self.class_id)
        if self.kind == AnnotationKind.EXCLUDED and self.cell_type_id is not None:
            raise ValueError(EXCLUDED_WITH_CELL_TYPE)
        if self.kind == AnnotationKind.CELL and self.cell_type_id is None:
            raise ValueError(CELL_TYPE_REQUIRED)
        return self


class AnnotationUpdate(BaseModel):
    """A switch to `excluded` clears the cell type; a switch to `cell` needs one."""

    kind: AnnotationKind | None = None
    cell_type_id: int | None = None
    class_id: int | str | None = None
    geometry: PolygonGeometry | None = None
    curve: CurveGeometry | None = None

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        self.cell_type_id = _resolve_class_id(self.cell_type_id, self.class_id)
        if self.kind == AnnotationKind.EXCLUDED and self.cell_type_id is not None:
            raise ValueError(EXCLUDED_WITH_CELL_TYPE)
        if self.kind == AnnotationKind.CELL and self.cell_type_id is None:
            raise ValueError(CELL_TYPE_REQUIRED)
        if (
            self.kind is None
            and self.cell_type_id is None
            and self.geometry is None
            and "curve" not in self.model_fields_set
        ):
            raise ValueError(ANNOTATION_UPDATE_EMPTY)
        return self


class AnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: AnnotationKind
    cell_type_id: int | None
    geometry: PolygonGeometry
    curve: CurveGeometry | None = None
    created_at: datetime
    # A cell lying wholly inside an excluded area of its hotspot (saved anyway; the client warns).
    inside_excluded: bool = False
    # Excluded areas merged into this one on save and gone since (the saved draft's own id when another area was
    # kept); the client removes them. Always empty for cells and slide annotations.
    absorbed_ids: list[uuid.UUID] = []

    @computed_field
    @property
    def class_id(self) -> str | None:
        return str(self.cell_type_id) if self.cell_type_id is not None else None


class AnnotationClassification(BaseModel):
    name: str
    color: tuple[int, int, int]


class AnnotationFeatureProperties(BaseModel):
    """QuPath-style feature properties."""

    objectType: Literal["annotation"] = "annotation"
    classification: AnnotationClassification
    kind: AnnotationKind = AnnotationKind.CELL


class AnnotationFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: uuid.UUID
    geometry: PolygonGeometry
    properties: AnnotationFeatureProperties


class AnnotationFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[AnnotationFeature]
