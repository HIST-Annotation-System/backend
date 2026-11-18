"""Basic efficiency benchmarks for the backend's CPU-bound services.

Run from the backend directory:

    .venv\\Scripts\\python.exe -m benchmarks.run_benchmarks [--slide PATH] [--quick]

Everything is in-process (no database, no HTTP, no object storage), so the numbers isolate the cost of the
code itself. Results are printed and saved to benchmarks/results.json.
"""

import argparse
import io
import json
import platform
import random
import statistics
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from openslide.deepzoom import DeepZoomGenerator
from PIL import Image
from shapely.geometry import box

from app.models.hotspot import SegmentationStatus
from app.models.priority_weight import PriorityComponent
from app.services.agreement import Cell, compute
from app.services.hotspot_features import compute_features
from app.services.priority import estimated_priorities
from app.services.slides import DZI_OVERLAP, DZI_TILE_SIZE
from app.services.slide_analysis import (
    detect_tissue_bbox,
    encode_jpeg,
    encode_png,
    open_slide_file,
    read_region_image,
    render_thumbnail,
)

HERE = Path(__file__).parent
DEFAULT_SLIDE_GLOB = "../.slide-cache/*/*.mrxs"
RESULTS: list[dict[str, Any]] = []


def bench(group: str, name: str, fn: Callable[[], object], repeat: int, warmup: int = 1, **params: Any) -> None:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    row = {
        "group": group,
        "name": name,
        "params": params,
        "runs": repeat,
        "mean_ms": statistics.fmean(samples),
        "median_ms": statistics.median(samples),
        "p95_ms": samples[min(len(samples) - 1, round(0.95 * (len(samples) - 1)))],
        "min_ms": samples[0],
        "max_ms": samples[-1],
        "ops_per_s": 1000 / statistics.median(samples) if statistics.median(samples) else float("inf"),
    }
    RESULTS.append(row)
    print(f"{group:<12} {name:<48} median {row['median_ms']:>10.3f} ms   p95 {row['p95_ms']:>10.3f} ms")


# --- image features -------------------------------------------------------------------------------------


def tissue_like_jpeg(size: int, seed: int = 0) -> bytes:
    """Pink/purple noise with some white glass: close enough to H&E for the feature maths."""
    rng = np.random.default_rng(seed)
    base = np.array([200, 120, 180], dtype=np.float64)
    image = base + rng.normal(0, 40, (size, size, 3))
    image[: size // 4] = 245  # a strip of blank glass
    buffer = io.BytesIO()
    Image.fromarray(np.clip(image, 0, 255).astype(np.uint8)).save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def bench_features(repeat: int, real_thumbnail: bytes | None) -> None:
    for size in (128, 256, 512):
        data = tissue_like_jpeg(size)
        bench("features", f"compute_features synthetic {size}x{size}", lambda d=data: compute_features(d), repeat, size=size)
    if real_thumbnail is not None:
        bench("features", "compute_features real slide thumbnail 512", lambda: compute_features(real_thumbnail), repeat)


# --- priority ranking -----------------------------------------------------------------------------------


def fake_hotspots(count: int, seed: int = 0) -> list[Any]:
    rng = random.Random(seed)
    return [
        SimpleNamespace(
            id=uuid.uuid4(),
            features={
                "entropy": rng.uniform(0, 8),
                "glcm_contrast": rng.uniform(0, 50),
                "edge_density": rng.uniform(0, 1),
                "hematoxylin": rng.uniform(0, 1),
                "tissue_fraction": rng.uniform(0, 1),
            },
            segmentation_status=SegmentationStatus.DONE if rng.random() < 0.7 else SegmentationStatus.PENDING,
            model_cell_count=rng.randint(0, 2000),
            mpp=0.25,
            image_width=1024,
            image_height=1024,
        )
        for _ in range(count)
    ]


def bench_priority(repeat: int) -> None:
    weights = {component: 1.0 for component in PriorityComponent}
    for count in (100, 1_000, 10_000, 50_000):
        hotspots = fake_hotspots(count)
        runs = repeat if count <= 10_000 else max(3, repeat // 4)
        bench("priority", f"estimated_priorities n={count}", lambda h=hotspots: estimated_priorities(h, weights), runs, hotspots=count)


# --- inter-annotator agreement --------------------------------------------------------------------------


def model_cells(count: int) -> list[Cell]:
    side = max(1, int(count**0.5))
    return [Cell(uuid.uuid4(), 1 + i % 3, box((i % side) * 20, (i // side) * 20, (i % side) * 20 + 12, (i // side) * 20 + 12)) for i in range(count)]


def annotator_layers(model: list[Cell], annotators: int, loose_share: float, seed: int = 0) -> dict[int, list[Cell]]:
    """Each annotator keeps copies of the model's cells (relabeling/reshaping a few) and redraws `loose_share` of
    them from scratch (no origin), which takes the slow overlap-matching path."""
    rng = random.Random(seed)
    layers: dict[int, list[Cell]] = {}
    for account in range(1, annotators + 1):
        cells = []
        for origin in model:
            if rng.random() < 0.05:
                continue  # removed
            jitter = rng.uniform(-1, 1)
            polygon = box(origin.polygon.bounds[0] + jitter, origin.polygon.bounds[1], origin.polygon.bounds[2] + jitter, origin.polygon.bounds[3])
            class_id = origin.class_id if rng.random() > 0.1 else 1 + (origin.class_id % 3)
            if rng.random() < loose_share:
                cells.append(Cell(uuid.uuid4(), class_id, polygon))
            else:
                cells.append(Cell(uuid.uuid4(), class_id, polygon, origin.id))
        layers[account] = cells
    return layers


def bench_agreement(repeat: int, quick: bool) -> None:
    for count in (50, 200, 1_000) if quick else (50, 200, 1_000, 2_000):
        model = model_cells(count)
        layers = annotator_layers(model, 3, loose_share=0.0)
        bench("agreement", f"compute copied cells, 3 annotators, n={count}", lambda m=model, l=layers: compute(m, l), max(3, repeat // 4), cells=count, loose=0.0)
    for count in (50, 100, 200) if quick else (50, 100, 200, 400):
        model = model_cells(count)
        layers = annotator_layers(model, 3, loose_share=1.0)
        bench("agreement", f"compute redrawn cells, 3 annotators, n={count}", lambda m=model, l=layers: compute(m, l), 3, cells=count, loose=1.0)
    model = model_cells(200)
    for annotators in (2, 5, 10):
        layers = annotator_layers(model, annotators, loose_share=0.1)
        bench("agreement", f"compute n=200, 10% redrawn, {annotators} annotators", lambda l=layers: compute(model, l), 5, annotators=annotators)


# --- whole-slide image ----------------------------------------------------------------------------------


def bench_slide(path: Path, repeat: int) -> bytes:
    bench("slide", "open_slide_file (+ close)", lambda: open_slide_file(path).close(), repeat)
    slide = open_slide_file(path)
    width, height = slide.dimensions
    print(f"    slide {path.name}: {width}x{height}, {slide.level_count} levels")
    RESULTS.append({"group": "slide", "name": "info", "dimensions": [width, height], "levels": slide.level_count, "file": path.name})

    bench("slide", "detect_tissue_bbox (2048 thumbnail)", lambda: detect_tissue_bbox(slide), max(3, repeat // 4))
    bbox = detect_tissue_bbox(slide)
    bench("slide", "render_thumbnail (512, tissue-centered)", lambda: render_thumbnail(slide, bbox), max(3, repeat // 4))
    thumbnail = render_thumbnail(slide, bbox)

    cx = (bbox["x"] + bbox["width"] // 2) if bbox else width // 2
    cy = (bbox["y"] + bbox["height"] // 2) if bbox else height // 2
    rng = random.Random(0)
    for region, max_size in ((256, 256), (1024, 512), (4096, 512), (16384, 1024)):
        points = [(cx + rng.randint(-region * 2, region * 2), cy + rng.randint(-region * 2, region * 2)) for _ in range(repeat + 1)]
        it = iter(points)
        bench(
            "slide",
            f"read_region_image {region}px -> {max_size}px (random, cold-ish)",
            lambda r=region, m=max_size, i=it: read_region_image(slide, *next(i), r, r, m),
            repeat,
            region=region,
            max_size=max_size,
        )
        bench(
            "slide",
            f"read_region_image {region}px -> {max_size}px (same spot, warm)",
            lambda r=region, m=max_size: read_region_image(slide, cx, cy, r, r, m),
            repeat,
            region=region,
            max_size=max_size,
        )
    # The viewer's hot path (SlideService.get_tile): one DeepZoom tile, encoded as JPEG.
    deepzoom = DeepZoomGenerator(slide, tile_size=DZI_TILE_SIZE, overlap=DZI_OVERLAP, limit_bounds=True)
    bench("tiles", "DeepZoomGenerator construction", lambda: DeepZoomGenerator(slide, tile_size=DZI_TILE_SIZE, overlap=DZI_OVERLAP, limit_bounds=True), repeat)
    top = deepzoom.level_count - 1
    for level in (top, top - 2, top - 4, top - 6):
        cols, rows = deepzoom.level_tiles[level]
        # Tiles around the tissue center at this level, as a pan would request them.
        scale = deepzoom.level_dimensions[level][0] / width
        col0 = min(cols - 1, int(cx * scale) // DZI_TILE_SIZE)
        row0 = min(rows - 1, int(cy * scale) // DZI_TILE_SIZE)
        addresses = [(min(cols - 1, max(0, col0 + dc)), min(rows - 1, max(0, row0 + dr))) for dr in range(-3, 3) for dc in range(-3, 3)]
        it = iter(addresses * 2)
        bench(
            "tiles",
            f"get_tile + encode_jpeg, DZI level {level} (top-{top - level})",
            lambda lv=level, i=it: encode_jpeg(deepzoom.get_tile(lv, next(i))),
            min(repeat, len(addresses) * 2 - 1),
            level=level,
        )
    tile = read_region_image(slide, cx, cy, 1024, 1024, 512)
    bench("encoding", "encode_jpeg 512x512 (q=85)", lambda: encode_jpeg(tile), repeat)
    bench("encoding", "encode_png 512x512", lambda: encode_png(tile), repeat)
    slide.close()
    return thumbnail


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slide", type=Path, help="An OpenSlide-readable slide (defaults to the first in .slide-cache)")
    parser.add_argument("--quick", action="store_true", help="Fewer repetitions and smaller agreement sizes")
    args = parser.parse_args()
    repeat = 10 if args.quick else 30

    slide = args.slide or next(iter(sorted(HERE.glob(DEFAULT_SLIDE_GLOB))), None)
    started = time.perf_counter()
    thumbnail = None
    if slide is not None and slide.exists():
        thumbnail = bench_slide(slide.resolve(), repeat)
    else:
        print("No slide found: skipping slide benchmarks")
    bench_features(repeat, thumbnail)
    bench_priority(repeat)
    bench_agreement(repeat, args.quick)

    output = {
        "machine": {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor()},
        "total_s": time.perf_counter() - started,
        "results": RESULTS,
    }
    (HERE / "results.json").write_text(json.dumps(output, indent=2))
    print(f"\nDone in {output['total_s']:.1f}s, saved to {HERE / 'results.json'}")


if __name__ == "__main__":
    main()
