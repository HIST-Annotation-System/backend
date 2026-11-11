import uuid

import pytest
from shapely.geometry import box

from app.services.agreement import Cell, changes_of, compute

TYPE_A, TYPE_B = 1, 2


def cell(x: float, class_id: int = TYPE_A, size: float = 10) -> Cell:
    return Cell(uuid.uuid4(), class_id, box(x, 0, x + size, size))


def copy_of(origin: Cell, class_id: int | None = None, shift: float | None = None) -> Cell:
    polygon = origin.polygon if shift is None else box(shift, 0, shift + 10, 10)
    return Cell(uuid.uuid4(), class_id or origin.class_id, polygon, origin.id)


def test_untouched_copies_agree_perfectly() -> None:
    model = [cell(0), cell(20, TYPE_B)]
    result = compute(model, {user: [copy_of(item) for item in model] for user in (1, 2, 3)})
    assert result.kappa == 1.0
    assert result.iou == 1.0


def test_nothing_drawn_is_perfect_agreement() -> None:
    result = compute([], {1: [], 2: [], 3: []})
    assert (result.kappa, result.iou) == (1.0, 1.0)


def test_one_relabeling_lowers_kappa_and_names_the_outlier() -> None:
    model = [cell(0), cell(20), cell(40, TYPE_B), cell(60, TYPE_B)]
    layers = {user: [copy_of(item) for item in model] for user in (1, 2, 3)}
    layers[3][0] = copy_of(model[0], TYPE_B)
    result = compute(model, layers)
    assert 0 < result.kappa < 1
    assert result.iou == 1.0
    assert result.annotators[3].kappa_without == 1.0
    assert result.annotators[1].kappa_without < 1.0
    assert result.annotators[3].changes["relabeled"] == [str(layers[3][0].id)]


def test_a_cell_one_annotator_removed_counts_as_not_annotated() -> None:
    model = [cell(0), cell(20)]
    layers = {user: [copy_of(item) for item in model] for user in (1, 2, 3)}
    del layers[3][1]
    result = compute(model, layers)
    assert result.kappa < 1
    # One of the two cells is missing from one annotator: 2 of its 3 pairs overlap fully, the others score 0.
    assert result.iou == pytest.approx((1 + 1 / 3) / 2)
    assert result.annotators[3].changes["removed"] == [str(model[1].id)]


def test_cells_removed_by_everyone_are_left_out() -> None:
    model = [cell(0), cell(20)]
    layers = {user: [copy_of(model[0])] for user in (1, 2, 3)}
    result = compute(model, layers)
    assert (result.kappa, result.iou) == (1.0, 1.0)


def test_new_shapes_that_overlap_are_the_same_object() -> None:
    result = compute([], {1: [cell(100)], 2: [cell(102)], 3: [cell(98)]})
    assert result.kappa == 1.0
    assert 0.5 < result.iou < 1.0
    # Far apart they are three objects, each seen by one annotator.
    apart = compute([], {1: [cell(0)], 2: [cell(50)], 3: [cell(100)]})
    assert apart.iou == 0.0
    assert apart.kappa < 1.0


def test_changes_against_the_model() -> None:
    model = [cell(0), cell(20), cell(40), cell(60)]
    kept, moved, relabeled = copy_of(model[0]), copy_of(model[1], shift=22), copy_of(model[2], TYPE_B)
    added = cell(200)
    changes = changes_of(model, [kept, moved, relabeled, added])
    assert changes == {
        "unchanged": [str(kept.id)],
        "reshaped": [str(moved.id)],
        "relabeled": [str(relabeled.id)],
        "removed": [str(model[3].id)],
        "added": [str(added.id)],
    }
