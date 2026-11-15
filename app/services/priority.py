"""The estimated priority of an unrated hotspot: which one an expert should look at first.

It is only a guess from the image and the model's cells, made of a few components that the admin weighs
(see `PriorityWeight`). Each component is turned into a percentile rank among the hotspots being listed, so scales
do not matter, and the ranks are averaged with the weights. A hotspot missing a component (say, the model has not
segmented it yet) is scored from the others.
"""

import uuid
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence

from app.exceptions.errors import InvalidInputError
from app.models.hotspot import Hotspot, HotspotFeature, SegmentationStatus
from app.models.priority_weight import PriorityComponent
from app.repositories.priority_weights import PriorityWeightRepository

# Hotspots with less tissue than this are not scored: they sort after every scored one.
MIN_TISSUE_FRACTION = 0.1
MICRONS_PER_MM = 1000
ALL_WEIGHTS_ZERO = "At least one component must keep a weight above 0, or no hotspot gets a priority."


def cell_count(hotspot: Hotspot) -> int | None:
    """Cells the model found; None until it has finished (a hotspot it skipped or failed has none)."""
    if hotspot.segmentation_status != SegmentationStatus.DONE:
        return None
    return hotspot.model_cell_count


def cell_density(hotspot: Hotspot) -> float | None:
    """Model cells per square millimetre; None without a finished segmentation or the image's resolution."""
    count = cell_count(hotspot)
    if count is None or not hotspot.mpp:
        return None
    area_mm2 = hotspot.image_width * hotspot.image_height * (hotspot.mpp / MICRONS_PER_MM) ** 2
    return count / area_mm2


def component_value(hotspot: Hotspot, component: PriorityComponent) -> float | None:
    if component == PriorityComponent.CELL_DENSITY:
        return cell_density(hotspot)
    return (hotspot.features or {}).get(component.value)


def _percentile_ranks(values: Sequence[float]) -> list[float]:
    """Each value's rank in 0..1 (ties share their middle rank; a lone value is 0.5)."""
    ordered = sorted(values)
    count = len(ordered)
    if count == 1:
        return [0.5]
    ranks = []
    for value in values:
        below = bisect_left(ordered, value)
        equal = bisect_right(ordered, value) - below
        ranks.append((below + (equal - 1) / 2) / (count - 1))
    return ranks


def estimated_priorities(
    hotspots: Sequence[Hotspot], weights: Mapping[PriorityComponent, float]
) -> dict[uuid.UUID, float]:
    """0..1 per hotspot; left out are those with too little tissue, or none of the weighted components."""
    scored = [
        hotspot
        for hotspot in hotspots
        if (hotspot.features or {}).get(HotspotFeature.TISSUE_FRACTION.value, 1.0) >= MIN_TISSUE_FRACTION
    ]
    totals: dict[uuid.UUID, float] = {}
    weight_sums: dict[uuid.UUID, float] = {}
    for component, weight in weights.items():
        if weight <= 0:
            continue
        with_value = [
            (hotspot, value) for hotspot in scored if (value := component_value(hotspot, component)) is not None
        ]
        ranks = _percentile_ranks([value for _, value in with_value])
        for (hotspot, _), rank in zip(with_value, ranks, strict=True):
            totals[hotspot.id] = totals.get(hotspot.id, 0.0) + weight * rank
            weight_sums[hotspot.id] = weight_sums.get(hotspot.id, 0.0) + weight
    return {hotspot_id: total / weight_sums[hotspot_id] for hotspot_id, total in totals.items()}


class PriorityWeightService:
    """The weights, read by every listing and changed only by the admin."""

    def __init__(self, weights: PriorityWeightRepository) -> None:
        self._weights = weights

    def get(self) -> dict[PriorityComponent, float]:
        stored = self._weights.as_dict()
        return {component: stored[component] for component in PriorityComponent if component in stored}

    def update(self, changes: Mapping[PriorityComponent, float]) -> dict[PriorityComponent, float]:
        """Raises InvalidInputError when that would switch every component off."""
        if not any(weight > 0 for weight in {**self.get(), **changes}.values()):
            raise InvalidInputError(ALL_WEIGHTS_ZERO)
        self._weights.set_weights(dict(changes))
        return self.get()
