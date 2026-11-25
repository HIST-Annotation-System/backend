import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from shapely.geometry import Polygon

from app.exceptions.errors import InvalidInputError
from app.models.hotspot import AnnotationKind
from app.schemas.annotations import CURVE_SEGMENT_STEPS, CurveGeometry, CurvePoint, PolygonGeometry
from app.services.annotations import (
    EXCLUDED_CLASS_NAME,
    build_feature_collection,
    cells_inside_excluded,
    clamp_curve,
    excluded_areas_overlap,
    fit_excluded_area,
    fit_shape_to_bounds,
    fit_to_bounds,
    flatten_curve,
    merge_excluded_areas,
)


def rect(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def shape(geometry: dict[str, Any]) -> Polygon:
    return Polygon(geometry["coordinates"][0])


def annotation(kind: AnnotationKind, geometry: dict[str, Any], cell_type_id: int | None = None) -> Any:
    return SimpleNamespace(id=uuid.uuid4(), kind=kind, geometry=geometry, cell_type_id=cell_type_id)


def triangle_curve(kind: str = "corner") -> CurveGeometry:
    return CurveGeometry(
        points=[
            CurvePoint(type=kind, point=(10, 10), in_handle=(5, 10), out_handle=(15, 10)),  # type: ignore[arg-type]
            CurvePoint(type="corner", point=(50, 10)),
            CurvePoint(type="corner", point=(30, 40)),
        ]
    )


class TestFitToBounds:
    def test_inside_shape_is_unchanged(self) -> None:
        fitted = fit_to_bounds(PolygonGeometry.model_validate(rect(1, 1, 5, 5)), 10, 10)
        assert fitted["coordinates"][0] == rect(1, 1, 5, 5)["coordinates"][0]

    def test_vertices_past_the_edge_are_clamped(self) -> None:
        fitted = fit_to_bounds(PolygonGeometry.model_validate(rect(-5, -5, 20, 20)), 10, 8)
        assert shape(fitted).bounds == (0, 0, 10, 8)

    def test_shape_wholly_outside_collapses_and_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError, match="outside the image"):
            fit_to_bounds(PolygonGeometry.model_validate(rect(20, 20, 30, 30)), 10, 10)


class TestCurves:
    def test_corner_only_curve_flattens_to_its_points(self) -> None:
        assert flatten_curve(triangle_curve()) == [(10, 10), (50, 10), (30, 40)]

    def test_each_segment_touching_a_smooth_point_is_subdivided(self) -> None:
        ring = flatten_curve(triangle_curve("curve"))
        # The smooth point touches two of the three segments.
        assert len(ring) == 3 + 2 * (CURVE_SEGMENT_STEPS - 1)
        assert ring[0] == (10, 10)

    def test_clamp_keeps_handle_direction(self) -> None:
        curve = CurveGeometry(
            points=[
                CurvePoint(type="curve", point=(95, 50), in_handle=(85, 50), out_handle=(105, 50)),
                CurvePoint(type="corner", point=(50, 90)),
                CurvePoint(type="corner", point=(10, 10)),
            ]
        )
        clamped = clamp_curve(curve, 100, 100).points[0]
        assert clamped.point == (95, 50)
        assert clamped.out_handle == (100, 50)  # shortened along +x to the edge
        assert clamped.in_handle == (85, 50)

    def test_fit_shape_without_curve_uses_the_polygon(self) -> None:
        geometry, curve = fit_shape_to_bounds(PolygonGeometry.model_validate(rect(0, 0, 5, 5)), None, 10, 10)
        assert curve is None
        assert shape(geometry).area == 25

    def test_fit_shape_with_curve_derives_the_polygon_from_it(self) -> None:
        ignored = PolygonGeometry.model_validate(rect(0, 0, 1, 1))
        geometry, curve = fit_shape_to_bounds(ignored, triangle_curve(), 100, 100)
        assert curve is not None and len(curve["points"]) == 3
        assert shape(geometry).area == pytest.approx(600)

    def test_fit_shape_clamps_a_curve_that_leaves_the_image(self) -> None:
        _, curve = fit_shape_to_bounds(None, triangle_curve(), 40, 30)
        assert curve is not None
        assert all(0 <= p["point"][0] <= 40 and 0 <= p["point"][1] <= 30 for p in curve["points"])


class TestExcludedAreas:
    def test_valid_area_is_kept_as_drawn(self) -> None:
        area = rect(1, 1, 5, 5)
        assert fit_excluded_area(area, 10, 10) == (area, False)

    def test_too_few_points(self) -> None:
        with pytest.raises(InvalidInputError, match="at least 3 points"):
            fit_excluded_area({"coordinates": [[[0, 0], [1, 1], [0, 0]]]}, 10, 10)

    def test_figure_eight_is_repaired_into_one_valid_ring(self) -> None:
        bowtie = {"coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]}
        fitted, repaired = fit_excluded_area(bowtie, 20, 20)
        assert repaired
        polygon = shape(fitted)
        assert polygon.is_valid
        assert polygon.area >= 50  # both loops survive

    def test_repaired_area_is_clipped_to_the_image(self) -> None:
        bowtie = {"coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]}
        fitted, _ = fit_excluded_area(bowtie, 5, 5)
        minx, miny, maxx, maxy = shape(fitted).bounds
        assert minx >= 0 and miny >= 0 and maxx <= 5 and maxy <= 5

    def test_degenerate_self_crossing_area_is_empty(self) -> None:
        line = {"coordinates": [[[0, 0], [5, 5], [10, 10], [5, 5], [0, 0]]]}
        with pytest.raises(InvalidInputError, match="non-zero area"):
            fit_excluded_area(line, 20, 20)

    @pytest.mark.parametrize(
        ("other", "expected"),
        [(rect(3, 3, 8, 8), True), (rect(5, 0, 9, 5), True), (rect(6, 6, 9, 9), False)],
        ids=["overlapping", "touching", "apart"],
    )
    def test_overlap(self, other: dict[str, Any], expected: bool) -> None:
        assert excluded_areas_overlap(rect(0, 0, 5, 5), other) is expected

    def test_merge_covers_all_areas(self) -> None:
        merged = shape(merge_excluded_areas([rect(0, 0, 5, 5), rect(4, 0, 9, 5)], 100, 100))
        assert merged.covers(Polygon([(0, 0), (9, 0), (9, 5), (0, 5)]))
        assert len(merged.interiors) == 0


class TestCellsInsideExcluded:
    def test_only_cells_wholly_inside_an_area(self) -> None:
        area = annotation(AnnotationKind.EXCLUDED, rect(0, 0, 10, 10))
        inside = annotation(AnnotationKind.CELL, rect(2, 2, 4, 4))
        straddling = annotation(AnnotationKind.CELL, rect(8, 8, 12, 12))
        outside = annotation(AnnotationKind.CELL, rect(20, 20, 22, 22))
        assert cells_inside_excluded([area, inside, straddling, outside]) == {inside.id}

    def test_no_areas_means_nothing_is_excluded(self) -> None:
        assert cells_inside_excluded([annotation(AnnotationKind.CELL, rect(0, 0, 1, 1))]) == set()

    def test_invalid_areas_are_ignored(self) -> None:
        bowtie = {"coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]}
        cell = annotation(AnnotationKind.CELL, rect(1, 4, 2, 6))
        assert cells_inside_excluded([annotation(AnnotationKind.EXCLUDED, bowtie), cell]) == set()


def test_feature_collection_is_qupath_shaped() -> None:
    cell = annotation(AnnotationKind.CELL, rect(0, 0, 2, 2), cell_type_id=1)
    unknown = annotation(AnnotationKind.CELL, rect(0, 0, 2, 2), cell_type_id=99)
    unclassified = annotation(AnnotationKind.CELL, rect(0, 0, 2, 2))
    area = annotation(AnnotationKind.EXCLUDED, rect(0, 0, 5, 5))
    collection = build_feature_collection(
        [cell, unknown, unclassified, area], [SimpleNamespace(id=1, name="Tumor")]  # type: ignore[list-item]
    ).model_dump(mode="json")

    assert collection["type"] == "FeatureCollection"
    names = [f["properties"]["classification"]["name"] for f in collection["features"]]
    assert names == ["Tumor", "Unclassified", "Unclassified", EXCLUDED_CLASS_NAME]
    assert collection["features"][0]["properties"]["objectType"] == "annotation"
    assert collection["features"][0]["properties"]["classification"]["color"] == [67, 131, 197]
    assert collection["features"][2]["properties"]["classification"]["color"] == [128, 128, 128]
    assert collection["features"][3]["properties"]["kind"] == "excluded"


def test_feature_colours_are_deterministic() -> None:
    first = build_feature_collection([annotation(AnnotationKind.CELL, rect(0, 0, 1, 1), 5)], [])
    second = build_feature_collection([annotation(AnnotationKind.CELL, rect(0, 0, 1, 1), 5)], [])
    assert first.features[0].properties.classification.color == second.features[0].properties.classification.color
