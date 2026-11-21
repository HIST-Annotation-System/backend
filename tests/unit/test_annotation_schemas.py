import math
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.hotspot import AnnotationKind
from app.schemas.annotations import (
    ANNOTATION_MAX_VERTICES,
    CURVE_SEGMENT_STEPS,
    AnnotationCreate,
    AnnotationRead,
    AnnotationUpdate,
    CurveGeometry,
    CurvePoint,
    PolygonGeometry,
)

SQUARE = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]]


def corner(x: float, y: float) -> dict[str, Any]:
    return {"type": "corner", "point": (x, y)}


class TestPolygonGeometry:
    def test_closes_an_open_ring(self) -> None:
        ring = PolygonGeometry(coordinates=SQUARE).coordinates[0]
        assert ring[0] == ring[-1] == (0.0, 0.0)
        assert len(ring) == 5

    def test_keeps_a_closed_ring_as_it_is(self) -> None:
        closed = [[*SQUARE[0], (0.0, 0.0)]]
        assert PolygonGeometry(coordinates=closed).coordinates == closed

    def test_drops_repeated_consecutive_vertices(self) -> None:
        ring = [[(0, 0), (0, 0), (10, 0), (10, 0), (10, 10), (0, 10)]]
        assert PolygonGeometry(coordinates=ring).coordinates[0] == [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]

    @pytest.mark.parametrize("rings", [[], [SQUARE[0], SQUARE[0]]])
    def test_needs_exactly_one_ring(self, rings: list[Any]) -> None:
        with pytest.raises(ValidationError, match="exactly one exterior ring"):
            PolygonGeometry(coordinates=rings)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_rejects_non_finite_coordinates(self, bad: float) -> None:
        with pytest.raises(ValidationError, match="finite"):
            PolygonGeometry(coordinates=[[(0, 0), (bad, 0), (1, 1)]])

    @pytest.mark.parametrize("ring", [[], [(1, 1)], [(0, 0), (1, 1)], [(0, 0), (1, 1), (0, 0)]])
    def test_needs_three_distinct_vertices(self, ring: list[Any]) -> None:
        with pytest.raises(ValidationError, match="at least 3 distinct"):
            PolygonGeometry(coordinates=[ring])

    def test_vertex_limit(self) -> None:
        def circle(n: int) -> list[tuple[float, float]]:
            return [(math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n)) for i in range(n)]

        PolygonGeometry(coordinates=[circle(ANNOTATION_MAX_VERTICES)])
        with pytest.raises(ValidationError, match="at most"):
            PolygonGeometry(coordinates=[circle(ANNOTATION_MAX_VERTICES + 1)])


class TestCurve:
    def test_corner_points_lose_their_handles(self) -> None:
        point = CurvePoint(type="corner", point=(1, 1), in_handle=(0, 0), out_handle=(2, 2))
        assert point.in_handle is None and point.out_handle is None

    def test_smooth_points_keep_their_handles(self) -> None:
        point = CurvePoint(type="curve", point=(1, 1), in_handle=(0, 0), out_handle=(2, 2))
        assert point.in_handle == (0, 0) and point.out_handle == (2, 2)

    def test_handles_must_be_finite(self) -> None:
        with pytest.raises(ValidationError, match="finite"):
            CurvePoint(type="curve", point=(1, 1), in_handle=(math.nan, 0))

    def test_needs_three_points(self) -> None:
        with pytest.raises(ValidationError, match="at least 3 points"):
            CurveGeometry(points=[CurvePoint(**corner(0, 0)), CurvePoint(**corner(1, 0))])

    def test_limit_counts_the_flattened_vertices(self) -> None:
        # Every segment touching a smooth point flattens to CURVE_SEGMENT_STEPS vertices.
        fits = ANNOTATION_MAX_VERTICES // CURVE_SEGMENT_STEPS
        smooth = [{"type": "curve", "point": (i, 0)} for i in range(fits)]
        CurveGeometry.model_validate({"points": smooth})
        with pytest.raises(ValidationError, match="flatten to at most"):
            CurveGeometry.model_validate({"points": [*smooth, {"type": "curve", "point": (fits, 0)}]})


class TestAnnotationCreate:
    def test_cell_needs_a_cell_type(self) -> None:
        with pytest.raises(ValidationError, match="cell_type_id is required"):
            AnnotationCreate(geometry=PolygonGeometry(coordinates=SQUARE))

    @pytest.mark.parametrize("class_id", [7, "7"])
    def test_class_id_is_an_alias_of_cell_type_id(self, class_id: int | str) -> None:
        created = AnnotationCreate(geometry=PolygonGeometry(coordinates=SQUARE), class_id=class_id)
        assert created.cell_type_id == 7

    def test_cell_type_id_wins_over_class_id(self) -> None:
        created = AnnotationCreate(geometry=PolygonGeometry(coordinates=SQUARE), cell_type_id=3, class_id="9")
        assert created.cell_type_id == 3

    def test_class_id_must_be_numeric(self) -> None:
        with pytest.raises(ValidationError, match="convertible to int"):
            AnnotationCreate(geometry=PolygonGeometry(coordinates=SQUARE), class_id="tumor")

    def test_excluded_area_has_no_cell_type(self) -> None:
        geometry = PolygonGeometry(coordinates=SQUARE)
        assert AnnotationCreate(kind=AnnotationKind.EXCLUDED, geometry=geometry).cell_type_id is None
        with pytest.raises(ValidationError, match="no cell type"):
            AnnotationCreate(kind=AnnotationKind.EXCLUDED, geometry=geometry, cell_type_id=1)


class TestAnnotationUpdate:
    def test_empty_update_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Provide kind"):
            AnnotationUpdate()

    @pytest.mark.parametrize(
        "change",
        [
            {"cell_type_id": 1},
            {"class_id": "2"},
            {"geometry": {"coordinates": SQUARE}},
            {"curve": None},
            {"confirmed": True},
            {"kind": "excluded"},
        ],
    )
    def test_any_single_change_is_enough(self, change: dict[str, Any]) -> None:
        AnnotationUpdate.model_validate(change)

    def test_switch_to_cell_needs_a_type(self) -> None:
        with pytest.raises(ValidationError, match="cell_type_id is required"):
            AnnotationUpdate(kind=AnnotationKind.CELL)

    def test_switch_to_excluded_refuses_a_type(self) -> None:
        with pytest.raises(ValidationError, match="no cell type"):
            AnnotationUpdate(kind=AnnotationKind.EXCLUDED, cell_type_id=1)

    def test_confirmed_can_only_be_true(self) -> None:
        with pytest.raises(ValidationError):
            AnnotationUpdate.model_validate({"confirmed": False})


def test_read_exposes_class_id_as_a_string() -> None:
    base = {
        "id": uuid.uuid4(),
        "kind": AnnotationKind.CELL,
        "geometry": {"coordinates": SQUARE},
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    assert AnnotationRead.model_validate({**base, "cell_type_id": 4}).model_dump()["class_id"] == "4"
    assert AnnotationRead.model_validate({**base, "cell_type_id": None}).class_id is None
