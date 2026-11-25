import uuid
from collections.abc import Iterable
from typing import Any

import shapely
from pydantic import ValidationError
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from app.exceptions.errors import InvalidInputError
from app.models.cell_type import CellType
from app.models.hotspot import AnnotationKind, HotspotAnnotation
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
EXCLUDED_AREA_TOO_FEW_POINTS = "An excluded area needs at least 3 points."
EXCLUDED_AREA_EMPTY = "An excluded area must have a non-zero area."
EXCLUDED_CLASS_NAME = "Excluded"
EXCLUDED_COLOR = (128, 128, 128)
# Repairing a self-crossing excluded area: growing it this much (px) merges loops that only touch at a point
# (a figure eight), and the simplification drops the extra vertices the growth adds along straight edges.
AREA_REPAIR_GROWTH = 0.5
AREA_REPAIR_SIMPLIFY = 0.25


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


def fit_excluded_area(geometry: dict[str, Any], width: float, height: float) -> tuple[dict[str, Any], bool]:
    """
    An excluded area ready to store (checked after clamping), and whether it had to be repaired. A valid ring is
    kept as drawn. A self-crossing one (a figure eight, a loop drawn by hand) would mask something undefined, so it
    becomes the one outline that covers everything drawn: single ring, no holes, inside the image, about half a
    pixel wider at the edge.
    """
    ring = geometry["coordinates"][0]
    if len(ring) < 4:
        raise InvalidInputError(EXCLUDED_AREA_TOO_FEW_POINTS)
    polygon = Polygon(ring)
    if polygon.is_valid:
        return geometry, False
    return _outline([polygon], width, height), True


def excluded_areas_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Whether two stored excluded areas cross or touch each other."""
    return bool(_area(first).intersects(_area(second)))


def merge_excluded_areas(areas: Iterable[dict[str, Any]], width: float, height: float) -> dict[str, Any]:
    """Overlapping or touching excluded areas as the one outline that covers them all (see `fit_excluded_area`)."""
    return _outline([_area(area) for area in areas], width, height)


def _area(geometry: dict[str, Any]) -> Any:
    return shapely.make_valid(Polygon(geometry["coordinates"][0]))


def _outline(shapes: list[Any], width: float, height: float) -> dict[str, Any]:
    """
    One ring covering every given shape: their areal parts joined (growing them merges loops or areas that only
    touch), the outer boundary of the largest piece (no holes), clipped to the image and simplified.
    """
    # A self-crossing ring's signed parts may cancel out, so the area is measured on its repaired parts.
    parts = [part for shape in shapes for part in _polygons(shapely.make_valid(shape)) if part.area > 0]
    if not parts:
        raise InvalidInputError(EXCLUDED_AREA_EMPTY)
    grown = unary_union(parts).buffer(AREA_REPAIR_GROWTH, join_style="mitre")
    outline = Polygon(_largest(_polygons(grown)).exterior)
    clipped = [part for part in _polygons(outline.intersection(box(0.0, 0.0, width, height))) if part.area > 0]
    if not clipped:
        raise InvalidInputError(EXCLUDED_AREA_EMPTY)
    repaired = _largest(clipped).simplify(AREA_REPAIR_SIMPLIFY)
    return _polygon([(x, y) for x, y in repaired.exterior.coords])


def _polygons(geometry: Any) -> list[Polygon]:
    """The polygons in any shapely result (a polygon, a multipolygon or a mixed collection, nested or not)."""
    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]
    return [polygon for part in getattr(geometry, "geoms", []) for polygon in _polygons(part)]


def _largest(polygons: list[Polygon]) -> Polygon:
    return max(polygons, key=lambda polygon: polygon.area)


def cells_inside_excluded(annotations: Iterable[HotspotAnnotation]) -> set[uuid.UUID]:
    """Ids of the cells lying wholly inside one of the given excluded areas."""
    items = list(annotations)
    areas = [
        Polygon(item.geometry["coordinates"][0]) for item in items if item.kind == AnnotationKind.EXCLUDED
    ]
    areas = [area for area in areas if area.is_valid]
    if not areas:
        return set()
    shapely.prepare(areas)
    return {
        item.id
        for item in items
        if item.kind == AnnotationKind.CELL
        and any(area.covers(shapely.make_valid(Polygon(item.geometry["coordinates"][0]))) for area in areas)
    }


def build_feature_collection(
    annotations: Iterable[HotspotAnnotation], cell_types: Iterable[CellType]
) -> AnnotationFeatureCollection:
    """QuPath-compatible GeoJSON of the given annotations (coordinates as stored)."""
    by_id = {item.id: item for item in cell_types}
    features = []
    for annotation in annotations:
        if annotation.kind == AnnotationKind.EXCLUDED:
            features.append(
                AnnotationFeature(
                    id=annotation.id,
                    geometry=PolygonGeometry.model_validate(annotation.geometry),
                    properties=AnnotationFeatureProperties(
                        classification=AnnotationClassification(name=EXCLUDED_CLASS_NAME, color=EXCLUDED_COLOR),
                        kind=AnnotationKind.EXCLUDED,
                    ),
                )
            )
            continue
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
