from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from app.exceptions.errors import InvalidInputError
from app.models.cell_type import CellType
from app.models.hotspot import HotspotAnnotation
from app.schemas.annotations import (
    CURVE_SEGMENT_STEPS,
    AnnotationClassification,
    AnnotationFeature,
    AnnotationFeatureCollection,
    AnnotationFeatureProperties,
    CurveGeometry,
    CurvePoint,
    PolygonGeometry,
    Position,
)

ANNOTATION_ALREADY_EXISTS = "Annotation {annotation_id} already exists."
CELL_TYPE_NOT_FOUND = "Cell type {cell_type_id} does not exist."
POLYGON_OUTSIDE_IMAGE = "The polygon lies outside the image."


def fit_to_bounds(geometry: PolygonGeometry, width: float, height: float) -> dict[str, Any]:
    """Clamp vertices to the image area [0, width] x [0, height] (shapes may be drawn past its edge)."""
    ring = [_clamp(point, width, height) for point in geometry.coordinates[0]]
    return _polygon(ring)


def clamp_curve(curve: CurveGeometry, width: float, height: float) -> CurveGeometry:
    """Move points and handles into the image; handles keep their direction, so smooth points stay smooth."""
    points = []
    for item in curve.points:
        point = _clamp(item.point, width, height)
        points.append(
            CurvePoint(
                type=item.type,
                point=point,
                in_handle=_pull_handle(item.in_handle, item.point, point, width, height),
                out_handle=_pull_handle(item.out_handle, item.point, point, width, height),
                locked=item.locked,
            )
        )
    return CurveGeometry(points=points)


def flatten_curve(curve: CurveGeometry) -> list[Position]:
    """Open polygon ring approximating the closed curve (the frontend flattens it the same way)."""
    points = curve.points
    ring: list[Position] = []
    for index, a in enumerate(points):
        b = points[(index + 1) % len(points)]
        ring.append(a.point)
        if a.type == "curve" or b.type == "curve":
            c1 = a.out_handle if a.type == "curve" and a.out_handle is not None else a.point
            c2 = b.in_handle if b.type == "curve" and b.in_handle is not None else b.point
            for step in range(1, CURVE_SEGMENT_STEPS):
                t = step / CURVE_SEGMENT_STEPS
                u = 1 - t
                x = u * u * u * a.point[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * b.point[0]
                y = u * u * u * a.point[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * b.point[1]
                ring.append((x, y))
    return ring


def fit_shape_to_bounds(
    geometry: PolygonGeometry | None, curve: CurveGeometry | None, width: float, height: float
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The (geometry, curve) pair to store; with a curve, the polygon is derived from it (geometry is ignored)."""
    if curve is None:
        assert geometry is not None, "A shape needs a polygon or a curve."
        return fit_to_bounds(geometry, width, height), None
    ring = flatten_curve(curve)
    if any(not (0.0 <= x <= width and 0.0 <= y <= height) for x, y in ring):
        curve = clamp_curve(curve, width, height)
        ring = flatten_curve(curve)
    return _polygon(ring), curve.model_dump(mode="json")


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


def _clamp(position: Position, width: float, height: float) -> Position:
    x, y = position
    return min(max(x, 0.0), width), min(max(y, 0.0), height)


def _pull_handle(
    handle: Position | None, original: Position, point: Position, width: float, height: float
) -> Position | None:
    """The handle moved with its clamped point and shortened along its direction to fit in the image."""
    if handle is None:
        return None
    dx, dy = handle[0] - original[0], handle[1] - original[1]
    scale = 1.0
    for offset, start, limit in ((dx, point[0], width), (dy, point[1], height)):
        if offset > 0:
            scale = min(scale, (limit - start) / offset)
        elif offset < 0:
            scale = min(scale, start / -offset)
    scale = max(scale, 0.0)
    return _clamp((point[0] + scale * dx, point[1] + scale * dy), width, height)


def _polygon(ring: list[Position]) -> dict[str, Any]:
    """The stored GeoJSON of a ring already inside the image."""
    try:
        return PolygonGeometry(coordinates=[ring]).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidInputError(POLYGON_OUTSIDE_IMAGE) from exc
