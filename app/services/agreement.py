"""How well annotators agree on a hotspot they reannotated, from the cells each of them confirmed.

Cells are grouped into items first: the copies of one model cell are one item (they say so themselves, `origin_id`),
and a cell drawn from scratch joins the item it overlaps most, at least `MATCH_IOU`, among those its author has no cell
in. Everything else is a new item. An item an annotator has no cell in counts as "not annotated" for them.

- Fleiss' kappa: items rated by the annotators, the category being the cell type (or "not annotated").
- IoU: per item, the mean overlap over all pairs of annotators (a pair with one missing cell scores 0, a pair with
  both missing is left out), then the mean over items.

Either is 1.0 when there is nothing to disagree about (no items, or a kappa that is undefined because everyone gave
every cell the same single category).
"""

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from statistics import fmean
from typing import Any

from shapely.geometry import Polygon

# Two cells are the same object when they overlap at least this much.
MATCH_IOU = 0.5
# Vertices closer than this (pixels) are the same vertex: a copy that was not edited equals its model cell.
SAME_SHAPE_TOLERANCE = 1e-3
CHANGES = ("unchanged", "reshaped", "relabeled", "removed", "added")

type Item = dict[int, Cell]


@dataclass(frozen=True)
class Cell:
    id: uuid.UUID
    class_id: int
    polygon: Polygon
    # The model cell this was copied from, when it was.
    origin_id: uuid.UUID | None = None


@dataclass(frozen=True)
class AnnotatorFigures:
    kappa_without: float
    iou_without: float
    iou_with_rest: float
    # What they did to the model's cells, as ids: unchanged/reshaped/relabeled/added are of their own cells, removed
    # are of the model's.
    changes: dict[str, list[str]]


@dataclass(frozen=True)
class Agreement:
    kappa: float
    iou: float
    annotators: dict[int, AnnotatorFigures]


def polygon_of(geometry: Mapping[str, Any]) -> Polygon:
    polygon = Polygon(geometry["coordinates"][0])
    return polygon if polygon.is_valid else polygon.buffer(0)


def overlap(first: Polygon, second: Polygon) -> float:
    union = first.union(second).area
    return first.intersection(second).area / union if union else 0.0


def group_cells(annotators: Mapping[int, Sequence[Cell]]) -> list[Item]:
    """Items in a fixed order, so the same cells always group the same way."""
    by_origin: dict[uuid.UUID, Item] = {}
    loose: list[tuple[int, Cell]] = []
    for account_id in sorted(annotators):
        for cell in sorted(annotators[account_id], key=lambda item: str(item.id)):
            if cell.origin_id is None:
                loose.append((account_id, cell))
            else:
                by_origin.setdefault(cell.origin_id, {}).setdefault(account_id, cell)
    items = list(by_origin.values())
    for account_id, cell in loose:
        matches = [
            (fmean(overlap(cell.polygon, member.polygon) for member in item.values()), -index)
            for index, item in enumerate(items)
            if account_id not in item
        ]
        score, negative_index = max(matches, default=(0.0, 0))
        if score >= MATCH_IOU:
            items[-negative_index][account_id] = cell
        else:
            items.append({account_id: cell})
    return items


def fleiss_kappa(items: Sequence[Item], annotators: Sequence[int]) -> float:
    raters = len(annotators)
    ratings = [
        Counter(item[account_id].class_id if account_id in item else None for account_id in annotators)
        for item in items
        if any(account_id in item for account_id in annotators)
    ]
    if raters < 2 or not ratings:
        return 1.0
    observed = fmean(
        (sum(count**2 for count in rating.values()) - raters) / (raters * (raters - 1)) for rating in ratings
    )
    totals = sum(ratings, Counter())
    expected = sum((count / (len(ratings) * raters)) ** 2 for count in totals.values())
    return 1.0 if expected >= 1 else (observed - expected) / (1 - expected)


def mean_iou(items: Sequence[Item], annotators: Sequence[int], only: int | None = None) -> float:
    """The mean IoU of the annotators' pairs, or of those that include `only`."""
    per_item = []
    for item in items:
        scores = [
            overlap(item[first].polygon, item[second].polygon) if first in item and second in item else 0.0
            for first, second in combinations(annotators, 2)
            if (only is None or only in (first, second)) and (first in item or second in item)
        ]
        if scores:
            per_item.append(fmean(scores))
    return fmean(per_item) if per_item else 1.0


def changes_of(model: Sequence[Cell], cells: Sequence[Cell]) -> dict[str, list[str]]:
    """What an annotator did to the model's cells (see `AnnotatorFigures.changes`)."""
    by_id = {cell.id: cell for cell in model}
    changes: dict[str, list[str]] = {name: [] for name in CHANGES}
    kept = set()
    for cell in cells:
        origin = by_id.get(cell.origin_id) if cell.origin_id else None
        if origin is None:
            changes["added"].append(str(cell.id))
            continue
        kept.add(origin.id)
        if not cell.polygon.equals_exact(origin.polygon, SAME_SHAPE_TOLERANCE):
            changes["reshaped"].append(str(cell.id))
        elif cell.class_id != origin.class_id:
            changes["relabeled"].append(str(cell.id))
        else:
            changes["unchanged"].append(str(cell.id))
    changes["removed"] = [str(origin.id) for origin in model if origin.id not in kept]
    return changes


def compute(model: Sequence[Cell], annotators: Mapping[int, Sequence[Cell]]) -> Agreement:
    """The agreement of `annotators` (account id -> the cells they confirmed) on a hotspot the model segmented."""
    ids = sorted(annotators)
    items = group_cells(annotators)
    return Agreement(
        kappa=fleiss_kappa(items, ids),
        iou=mean_iou(items, ids),
        annotators={
            account_id: AnnotatorFigures(
                kappa_without=fleiss_kappa(items, [other for other in ids if other != account_id]),
                iou_without=mean_iou(items, [other for other in ids if other != account_id]),
                iou_with_rest=mean_iou(items, ids, only=account_id),
                changes=changes_of(model, annotators[account_id]),
            )
            for account_id in ids
        },
    )
