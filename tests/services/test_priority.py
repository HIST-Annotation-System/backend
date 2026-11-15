import io
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from app.models.hotspot import SegmentationStatus
from app.models.priority_weight import PriorityComponent
from app.services.hotspot_features import compute_features
from app.services.priority import cell_density, estimated_priorities


def hotspot(features: dict[str, float] | None = None, **fields: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "features": features,
        "segmentation_status": SegmentationStatus.PENDING,
        "model_cell_count": 0,
        "mpp": None,
        "image_width": 1000,
        "image_height": 1000,
    }
    return SimpleNamespace(**{**defaults, **fields})


def jpeg(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buffer, "JPEG")
    return buffer.getvalue()


def test_blank_glass_has_no_tissue_and_no_hematoxylin() -> None:
    features = compute_features(jpeg((250, 250, 250)))
    assert features["tissue_fraction"] == 0.0
    assert features["hematoxylin"] < 0.05


def test_purple_stain_counts_as_hematoxylin() -> None:
    purple = compute_features(jpeg((90, 60, 150)))
    pink = compute_features(jpeg((235, 150, 190)))
    assert purple["tissue_fraction"] == 1.0
    assert purple["hematoxylin"] > pink["hematoxylin"] > 0


def test_cell_density_needs_a_finished_segmentation_and_a_resolution() -> None:
    done = hotspot(segmentation_status=SegmentationStatus.DONE, model_cell_count=50, mpp=1.0)
    assert cell_density(done) == pytest.approx(50 / 1.0)  # 1000 x 1000 px at 1 micron = 1 mm2
    assert cell_density(hotspot(model_cell_count=50, mpp=1.0)) is None
    assert cell_density(hotspot(segmentation_status=SegmentationStatus.DONE, model_cell_count=50)) is None
    assert cell_density(hotspot(segmentation_status=SegmentationStatus.DONE, model_cell_count=0, mpp=1.0)) == 0


def test_priority_averages_percentile_ranks_by_weight() -> None:
    low, mid, high = (hotspot({"entropy": value, "edge_density": 10 - value}) for value in (1, 2, 3))
    weights = {PriorityComponent.ENTROPY: 3.0, PriorityComponent.EDGE_DENSITY: 1.0}
    priorities = estimated_priorities([low, mid, high], weights)
    assert priorities[high.id] == pytest.approx((3 * 1.0 + 1 * 0.0) / 4)
    assert priorities[mid.id] == pytest.approx(0.5)
    assert priorities[low.id] == pytest.approx((3 * 0.0 + 1 * 1.0) / 4)


def test_a_missing_component_is_scored_from_the_rest() -> None:
    unsegmented = hotspot({"entropy": 5.0})
    segmented = hotspot(
        {"entropy": 5.0}, segmentation_status=SegmentationStatus.DONE, model_cell_count=20, mpp=0.5
    )
    weights = {PriorityComponent.ENTROPY: 1.0, PriorityComponent.CELL_DENSITY: 1.0}
    priorities = estimated_priorities([unsegmented, segmented], weights)
    # Entropy ties at 0.5; the lone cell density ranks 0.5 too; the unsegmented one just keeps its entropy rank.
    assert priorities[unsegmented.id] == pytest.approx(0.5)
    assert priorities[segmented.id] == pytest.approx(0.5)


def test_zero_weights_and_empty_crops_are_left_out() -> None:
    blank = hotspot({"entropy": 7.0, "tissue_fraction": 0.02})
    unmeasured = hotspot(None)
    off = hotspot({"entropy": 3.0})
    priorities = estimated_priorities([blank, unmeasured, off], {PriorityComponent.ENTROPY: 0.0})
    assert priorities == {}
    priorities = estimated_priorities([blank, unmeasured, off], {PriorityComponent.ENTROPY: 1.0})
    assert set(priorities) == {off.id}
