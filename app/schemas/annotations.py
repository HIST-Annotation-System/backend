import math
import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

ANNOTATION_MAX_VERTICES = 10_000
POLYGON_SINGLE_RING = "A polygon must have exactly one exterior ring (holes are not supported)."
POLYGON_NOT_FINITE = "Coordinates must be finite numbers."
POLYGON_TOO_FEW_VERTICES = "A polygon needs at least 3 distinct vertices."
POLYGON_TOO_MANY_VERTICES = f"A polygon may have at most {ANNOTATION_MAX_VERTICES} vertices."
ANNOTATION_UPDATE_EMPTY = "Provide cell_type_id and/or geometry."

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


class AnnotationCreate(BaseModel):
    # The annotator's own UUID (keeps its undo/redo history consistent); generated when omitted.
    id: uuid.UUID | None = None
    cell_type_id: int | None = None
    class_id: int | str | None = None
    geometry: PolygonGeometry

    @model_validator(mode="after")
    def _resolve_cell_type(self) -> Self:
        if self.cell_type_id is None and self.class_id is not None:
            try:
                self.cell_type_id = int(self.class_id)
            except ValueError:
                raise ValueError("class_id must be convertible to int")
        if self.cell_type_id is None:
            raise ValueError("cell_type_id is required")
        return self


class AnnotationUpdate(BaseModel):
    cell_type_id: int | None = None
    class_id: int | str | None = None
    geometry: PolygonGeometry | None = None

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        if self.cell_type_id is None and self.class_id is not None:
            try:
                self.cell_type_id = int(self.class_id)
            except ValueError:
                raise ValueError("class_id must be convertible to int")
        if self.cell_type_id is None and self.geometry is None:
            raise ValueError(ANNOTATION_UPDATE_EMPTY)
        return self


class AnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cell_type_id: int | None
    geometry: PolygonGeometry
    created_at: datetime

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


class AnnotationFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: uuid.UUID
    geometry: PolygonGeometry
    properties: AnnotationFeatureProperties


class AnnotationFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[AnnotationFeature]
