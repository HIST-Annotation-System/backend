# Backend efficiency tests: results

**Date:** 2026-11-18
**Script:** `benchmarks/run_benchmarks.py` (raw numbers in `benchmarks/results.json`)
**Machine:** Intel Core i7-1355U (laptop, 10 cores), 16 GB RAM, Windows 11, Python 3.12.14
**Test slide:** `Mirax2.2-1.mrxs` from `.slide-cache`: 101 832 × 219 976 px, 10 pyramid levels

## How to run

```powershell
cd backend
uv sync                                         # the venv was missing shapely/scipy before this
.venv\Scripts\python.exe -m benchmarks.run_benchmarks           # full run, about 2.5 min
.venv\Scripts\python.exe -m benchmarks.run_benchmarks --quick   # fewer runs, smaller sizes
.venv\Scripts\python.exe -m benchmarks.run_benchmarks --slide path\to\slide.mrxs
```

The benchmarks call the service functions directly, in one process. They don't touch the database, HTTP or S3, so
they measure only the cost of the code itself. Each case runs once as a warm-up and then 30 timed times (fewer for
slow cases). The tables show the median and the p95.

> **On noise:** this is a laptop CPU with aggressive power management. Across three runs, absolute times varied
> by up to **2–3×**. The first full run was roughly twice as slow as the one below. In one run the n=2000 case even
> finished faster than n=1000. Treat the numbers as orders of magnitude, and trust the **scaling trends** (which were
> consistent across runs) more than any single value.

---

## 1. Slide viewing (OpenSlide)

| Operation | Median | p95 |
|---|---:|---:|
| `open_slide_file` (MRXS open + close) | 157 ms | 229 ms |
| `DeepZoomGenerator(...)` construction | 0.16 ms | 0.30 ms |
| **Viewer tile:** `get_tile` + JPEG, top level (full resolution) | **3.8 ms** | 17.5 ms |
| Viewer tile, 2 levels below the top (4× zoomed out) | 13.1 ms | 16.8 ms |
| Viewer tile, 4 levels below the top (16× zoomed out) | 16.4 ms | 20.2 ms |
| Viewer tile, 6 levels below the top (64× zoomed out) | 20.3 ms | 25.2 ms |
| `read_region_image` 256 px → 256 (random spot / same spot) | 1.3 / 0.9 ms | 8.7 / 1.5 ms |
| `read_region_image` 1024 px → 512 | 30 / 19 ms | 53 / 27 ms |
| `read_region_image` 4096 px → 512 | 41 / 37 ms | 74 / 50 ms |
| `read_region_image` 16384 px → 1024 | 216 / 174 ms | 266 / 216 ms |
| `detect_tissue_bbox` (2048 px thumbnail + labelling) | 678 ms | 876 ms |
| `render_thumbnail` (512 px, tissue-centred) | 147 ms | 196 ms |

**Findings**
- **Tile serving is fast:** 4–20 ms per tile on one core. One worker can serve about 50–250 tiles/s, so the viewer
  should feel smooth. Zoomed-out tiles cost more (~5×) because OpenSlide has to downsample from the nearest pyramid
  level.
- Opening an MRXS slide costs about 150–380 ms. `SlideService._open` already caches handles, so users pay this only
  on the first tile request for each slide.
- Tissue detection plus the thumbnail cost about 0.8–1.5 s per slide. This runs only once, at ingest, so it's fine.

## 2. Image encoding

| Operation (512×512 RGB) | Median | p95 |
|---|---:|---:|
| `encode_jpeg` (q=85) | 4.1 ms | 4.5 ms |
| `encode_png` | 45 ms | 61 ms |

**Finding:** PNG is about **11× slower** than JPEG (and its files are much larger). `export_region` (`slides.py:176`)
always returns PNG, at up to 2048×2048. That's about 16× the pixels of this test, so expect roughly 0.5–1 s of
encoding alone. This is acceptable for an occasional export. Consider `compress_level=1` or JPEG if the endpoint
gets heavy use.

## 3. Hotspot image features (`compute_features`)

| Input | Median | p95 |
|---|---:|---:|
| 128 × 128 synthetic | 3.5 ms | 5.8 ms |
| 256 × 256 synthetic | 13.2 ms | 18.0 ms |
| 512 × 512 synthetic | 65 ms | 78 ms |
| 512 × 512 real slide thumbnail | 63 ms | 76 ms |

**Findings**
- Cost grows linearly with the pixel count (4× the pixels takes about 4–5× the time). At the stored thumbnail size
  (512), each hotspot takes about 60–120 ms, computed once when it is created. That's fine.
- The profiler shows `hematoxylin()` (colour deconvolution: log plus matrix multiply) and `glcm_contrast()` take
  about 55% of the time. Using `float32` instead of `float64` would roughly halve this if it ever matters.

## 4. Estimated priority ranking (`estimated_priorities`)

| Hotspots | Median | p95 |
|---:|---:|---:|
| 100 | 0.9 ms | 1.6 ms |
| 1 000 | 10.8 ms | 16.2 ms |
| 10 000 | 189 ms | 279 ms |
| 50 000 | 2.4 s | 2.8 s |

**Findings**
- Cost scales about as n log n, roughly 2–5 µs per hotspot per component, and it is recomputed on every catalog
  listing.
- Up to a few thousand hotspots this is negligible. **Above ~10 000 hotspots in one listing it adds noticeable
  latency** (0.2 s or more). At that point, cache the ranks or store the priorities after each weights change or
  segmentation, rather than computing them per request.

## 5. Inter-annotator agreement (`agreement.compute`)

All cases use box-shaped cells. "Copied" means every annotator kept the model's cells, with jitter, relabels and
removals, so cells are grouped by `origin_id`. "Redrawn" means every cell was drawn from scratch, so they go through
the overlap-matching path.

| Case | Median | p95 |
|---|---:|---:|
| Copied cells, 3 annotators, n=50 | 98 ms | 105 ms |
| Copied cells, 3 annotators, n=200 | 407 ms | 429 ms |
| Copied cells, 3 annotators, n=1000 | 2.1 s | 2.5 s |
| Copied cells, 3 annotators, n=2000 | 1.6–4.4 s* | 2.1–5.0 s |
| Redrawn cells, 3 annotators, n=50 | 57 ms | 76 ms |
| Redrawn cells, 3 annotators, n=100 | 146 ms | 150 ms |
| Redrawn cells, 3 annotators, n=200 | 457 ms | 469 ms |
| Redrawn cells, 3 annotators, n=400 | 1.5 s | 1.5 s |
| n=200, 10% redrawn, **2** annotators | 49 ms | 66 ms |
| n=200, 10% redrawn, **5** annotators | 782 ms | 866 ms |
| n=200, 10% redrawn, **10** annotators | **6.0 s** | 6.2 s |

\* Range across two runs (CPU throttling, see the note on noise above).

**Findings: this is the main bottleneck in the backend.**
1. **Redrawn cells scale quadratically.** Doubling n costs about 3× (50→100→200→400: 57 → 146 → 457 → 1506 ms).
   `group_cells` compares every loose cell with every existing item using a shapely union and intersection.
   *Fix:* build a `shapely.STRtree` over the items' polygons and test only the candidates whose bounding boxes
   intersect. That makes the cost close to linear.
2. **Cost grows roughly with the cube of the annotator count** (2 → 5 → 10 annotators: 49 ms → 0.78 s → 6.0 s).
   `compute` calls `mean_iou` 1 + 2·A times, and each call recomputes the IoU of every annotator pair from
   scratch. The profiler shows that for 200 cells and 3 annotators, `overlap()` runs 2 128 times but only about 570
   pairs are distinct. *Fix:* compute each pair's IoU once per item (for example, in a dict keyed by
   `(item_index, a, b)`) and reuse it in all `mean_iou` calls. That would bring the cost down to about A², roughly
   4× faster for 3 annotators and about 20× faster for 10.
3. Even the cheap path costs about 1–2 ms per cell, because every IoU pays for two GEOS calls (`union` and
   `intersection`). You can get the union area as `a.area + b.area - inter.area`, which saves one GEOS operation per
   pair (about 45% of the time in the profile).

With realistic hotspots (a few hundred cells and 2–3 annotators), it already takes 0.1–0.5 s. If this runs in a
request rather than in a Celery task, it is the first thing to optimise.

---

## Summary

| Area | Verdict | Action |
|---|---|---|
| Viewer tiles (DeepZoom + JPEG) | ✅ 4–20 ms per tile | None |
| Slide open | ✅ ~150–380 ms, cached | None |
| Ingest (tissue detection + thumbnail) | ✅ ~1 s per slide, once | None |
| Hotspot features | ✅ ~60–120 ms per hotspot, once | Optional: `float32` |
| Region export (PNG) | ⚠️ PNG is 11× slower than JPEG | Lower `compress_level` or allow JPEG |
| Priority ranking | ⚠️ fine below 10k hotspots | Cache or persist for large catalogs |
| Agreement | ❌ O(n²) in redrawn cells, ~O(A³) in annotators | STRtree matching + caching pairwise IoU |

## Limitations

- These are single-threaded micro-benchmarks. They don't measure concurrency, the database, network or S3 download
  time (`SlideFileService.materialize`), or the ML worker.
- One slide (MRXS) only. Other formats (SVS, NDPI, TIFF) have different open and tile costs.
- The agreement and priority inputs are synthetic. Real cell polygons have more vertices than boxes, so the real
  agreement costs will be **higher** than shown.
- The laptop CPU throttles, so the absolute numbers vary between runs by up to 2–3×.
