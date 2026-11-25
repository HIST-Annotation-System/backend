import io
from typing import Any

import numpy as np
import openslide
import pytest
from PIL import Image

from app.exceptions.errors import InvalidInputError
from app.models.hotspot import HotspotFeature, HotspotImageType
from app.services import hotspot_images, slide_ingest
from app.services.hotspot_features import (
    compute_features,
    edge_density,
    glcm_contrast,
    hematoxylin,
    shannon_entropy,
    tissue_fraction,
)
from app.services.hotspot_images import THUMBNAIL_SIZE, decode_upload, encode_captured, render_thumbnail
from app.services.slide_analysis import _square_crop, detect_tissue_bbox, read_region_image
from app.services.slide_ingest import _require_usable_slide, _to_float


def encoded(image: Image.Image, fmt: str, **options: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def checkerboard(size: int = 32) -> np.ndarray:
    return (np.indices((size, size)).sum(axis=0) % 2 * 255).astype(np.float64)


class TestFeatures:
    def test_flat_image(self) -> None:
        flat = np.full((32, 32), 128.0)
        assert shannon_entropy(flat) == 0.0
        assert glcm_contrast(flat) == 0.0
        assert edge_density(flat) == 0.0

    def test_entropy_of_an_even_spread_is_eight_bits(self) -> None:
        assert shannon_entropy(np.arange(256, dtype=np.float64).reshape(16, 16)) == pytest.approx(8.0)

    def test_entropy_of_two_levels_is_one_bit(self) -> None:
        assert shannon_entropy(checkerboard()) == pytest.approx(1.0)

    def test_checkerboard_has_high_contrast_and_edges(self) -> None:
        assert glcm_contrast(checkerboard()) > 10_000
        assert edge_density(checkerboard()) > 0

    def test_contrast_of_a_single_pixel_is_zero(self) -> None:
        assert glcm_contrast(np.zeros((1, 1))) == 0.0

    def test_tissue_fraction(self) -> None:
        gray = np.full((10, 10), 255.0)
        assert tissue_fraction(gray) == 0.0
        gray[:5] = 100
        assert tissue_fraction(gray) == pytest.approx(0.5)

    def test_hematoxylin_is_higher_for_purple_than_for_glass(self) -> None:
        glass = np.full((4, 4, 3), 255.0)
        purple = np.tile(np.array([90.0, 60.0, 150.0]), (4, 4, 1))
        assert hematoxylin(glass) == pytest.approx(0.0, abs=1e-6)
        assert hematoxylin(purple) > 0.1

    def test_compute_features_returns_every_feature(self) -> None:
        image = Image.fromarray(np.random.default_rng(0).integers(0, 255, (64, 64, 3), dtype=np.uint8))
        features = compute_features(encoded(image, "JPEG"))
        assert set(features) == {feature.value for feature in HotspotFeature}
        assert all(isinstance(value, float) for value in features.values())


class TestDecodeUpload:
    def test_png_is_kept_byte_for_byte(self) -> None:
        data = encoded(Image.new("RGB", (40, 30), "red"), "PNG")
        result = decode_upload(data, "a.png")
        assert result.data == data
        assert (result.image_type, result.width, result.height) == (HotspotImageType.PNG, 40, 30)
        assert Image.open(io.BytesIO(result.thumbnail)).format == "JPEG"

    def test_jpeg(self) -> None:
        result = decode_upload(encoded(Image.new("RGB", (10, 10)), "JPEG"), "a.jpg")
        assert result.image_type == HotspotImageType.JPEG

    def test_the_content_decides_not_the_extension(self) -> None:
        result = decode_upload(encoded(Image.new("RGB", (10, 10)), "PNG"), "misnamed.jpg")
        assert result.image_type == HotspotImageType.PNG

    @pytest.mark.parametrize(
        "data",
        [b"not an image", encoded(Image.new("RGB", (4, 4)), "GIF"), encoded(Image.new("RGB", (4, 4)), "BMP")],
        ids=["garbage", "gif", "bmp"],
    )
    def test_other_files_are_rejected(self, data: bytes) -> None:
        with pytest.raises(InvalidInputError, match="'x' is not a PNG or JPEG"):
            decode_upload(data, "x")

    def test_too_many_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hotspot_images, "UPLOAD_MAX_BYTES", 10)
        with pytest.raises(InvalidInputError, match="larger than"):
            decode_upload(encoded(Image.new("RGB", (10, 10)), "PNG"), "a.png")

    def test_too_many_pixels_is_checked_before_decoding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hotspot_images, "UPLOAD_MAX_PIXELS", 99)
        with pytest.raises(InvalidInputError, match="more than"):
            decode_upload(encoded(Image.new("RGB", (10, 10)), "PNG"), "a.png")

    def test_oversized_image_is_scaled_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hotspot_images, "UPLOAD_MAX_SIDE", 50)
        result = decode_upload(encoded(Image.new("RGB", (200, 100)), "PNG"), "a.png")
        assert (result.width, result.height) == (50, 25)
        assert Image.open(io.BytesIO(result.data)).size == (50, 25)

    def test_exif_rotation_is_applied(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90° clockwise to view
        data = encoded(Image.new("RGB", (40, 20)), "JPEG", exif=exif)
        result = decode_upload(data, "phone.jpg")
        assert (result.width, result.height) == (20, 40)
        assert result.data != data


class TestThumbnails:
    def test_fits_in_the_thumbnail_size_keeping_the_aspect(self) -> None:
        thumbnail = Image.open(io.BytesIO(render_thumbnail(Image.new("RGB", (1024, 512)))))
        assert thumbnail.size == (THUMBNAIL_SIZE, THUMBNAIL_SIZE // 2)

    def test_transparency_is_flattened_onto_white(self) -> None:
        thumbnail = Image.open(io.BytesIO(render_thumbnail(Image.new("RGBA", (8, 8), (0, 0, 0, 0)))))
        assert thumbnail.mode == "RGB"
        assert min(thumbnail.getpixel((4, 4))) > 245  # type: ignore[arg-type]

    def test_captured_square_is_stored_as_png(self) -> None:
        result = encode_captured(Image.new("RGB", (64, 64)))
        assert result.image_type == HotspotImageType.PNG
        assert Image.open(io.BytesIO(result.data)).format == "PNG"


class FakeSlide:
    """The part of an OpenSlide the analysis helpers use: one level, an RGB image in memory."""

    def __init__(self, image: Image.Image) -> None:
        self.image = image.convert("RGBA")
        self.dimensions = image.size
        self.level_downsamples = (1.0,)
        self.properties: dict[str, str] = {}

    def get_thumbnail(self, size: tuple[int, int]) -> Image.Image:
        copy = self.image.copy()
        copy.thumbnail(size)
        return copy

    def get_best_level_for_downsample(self, downsample: float) -> int:
        return 0

    def read_region(self, location: tuple[int, int], level: int, size: tuple[int, int]) -> Image.Image:
        x, y = location
        return self.image.crop((x, y, x + size[0], y + size[1]))


class TestSlideAnalysis:
    def test_blank_slide_has_no_tissue(self) -> None:
        assert detect_tissue_bbox(FakeSlide(Image.new("RGB", (100, 100), "white"))) is None  # type: ignore[arg-type]

    def test_tissue_box_ignores_dust(self) -> None:
        image = Image.new("RGB", (100, 100), "white")
        image.paste((120, 60, 140), (20, 30, 60, 70))  # tissue
        image.putpixel((95, 95), (0, 0, 0))  # a speck of dust
        assert detect_tissue_bbox(FakeSlide(image)) == {"x": 20, "y": 30, "width": 40, "height": 40}  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("box", "slide", "expected"),
        [
            ((40, 40, 20, 10), (100, 100), (40, 35, 20)),  # centred
            ((0, 0, 10, 40), (100, 100), (0, 0, 40)),  # clamped at the top-left edge
            ((90, 90, 10, 20), (100, 100), (80, 80, 20)),  # clamped at the bottom-right edge
            ((0, 45, 200, 10), (100, 100), (0, -50, 200)),  # wider than the slide: centring wins
            ((0, 0, 0, 0), (10, 10), (0, 0, 1)),  # empty box
        ],
    )
    def test_square_crop(
        self, box: tuple[int, int, int, int], slide: tuple[int, int], expected: tuple[int, int, int]
    ) -> None:
        assert _square_crop(*box, *slide) == expected

    def test_read_region_scales_to_the_max_size_and_paints_unscanned_area(self) -> None:
        slide = FakeSlide(Image.new("RGBA", (100, 100), (0, 0, 0, 0)))
        slide.properties[openslide.PROPERTY_NAME_BACKGROUND_COLOR] = "ff0000"
        image = read_region_image(slide, 0, 0, 100, 50, 20)  # type: ignore[arg-type]
        assert image.size == (20, 10)
        assert image.getpixel((5, 5)) == (255, 0, 0)


class TestSlideIngestChecks:
    @pytest.mark.parametrize(("value", "expected"), [("0.25", 0.25), (None, None), ("n/a", None), ("", None)])
    def test_to_float(self, value: str | None, expected: float | None) -> None:
        assert _to_float(value) == expected

    def mpp(self, x: str | None = "0.25", y: str | None = "0.25") -> dict[str, str]:
        values = {openslide.PROPERTY_NAME_MPP_X: x, openslide.PROPERTY_NAME_MPP_Y: y}
        return {key: value for key, value in values.items() if value is not None}

    def test_small_flat_slide_with_resolution_is_usable(self) -> None:
        _require_usable_slide(self.mpp(), (5_000, 5_000), 1)

    @pytest.mark.parametrize(("x", "y"), [(None, "0.25"), ("0.25", None), ("0", "0.25"), ("nan", "0.25"), ("-1", "1")])
    def test_needs_a_positive_resolution_on_both_axes(self, x: str | None, y: str | None) -> None:
        with pytest.raises(InvalidInputError, match="MPP"):
            _require_usable_slide(self.mpp(x, y), (100, 100), 1)

    def test_large_slide_needs_a_pyramid(self) -> None:
        with pytest.raises(InvalidInputError, match="not pyramidal"):
            _require_usable_slide(self.mpp(), (slide_ingest.PYRAMID_REQUIRED_ABOVE_PX + 1, 10), 1)
        _require_usable_slide(self.mpp(), (slide_ingest.PYRAMID_REQUIRED_ABOVE_PX + 1, 10), 3)

    def test_vendor_must_match_the_extension(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        detected: dict[str, str | None] = {"vendor": "generic-tiff"}
        monkeypatch.setattr(
            slide_ingest.openslide.OpenSlide, "detect_format", staticmethod(lambda path: detected["vendor"])
        )
        with pytest.raises(InvalidInputError, match="generic-tiff data"):
            slide_ingest._require_matching_vendor(tmp_path / "a.svs")
        slide_ingest._require_matching_vendor(tmp_path / "a.tif")
        detected["vendor"] = None
        with pytest.raises(InvalidInputError, match="not a readable"):
            slide_ingest._require_matching_vendor(tmp_path / "a.tif")

