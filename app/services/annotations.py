from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from app.exceptions.errors import InvalidInputError
from app.models.cell_type import CellType
from app.models.hotspot import HotspotAnnotation
from app.schemas.annotations import (
    AnnotationClassification,
    AnnotationFeature,
    AnnotationFeatureCollection,
    AnnotationFeatureProperties,
    PolygonGeometry,
)

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


def build_feature_collection(
    annotations: Iterable[HotspotAnnotation], cell_types: Iterable[CellType]
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
