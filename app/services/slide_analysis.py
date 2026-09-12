"""Image helpers over an opened slide: opening, encoding, thumbnails, tissue detection and region reads."""

import io
import math
from pathlib import Path

import numpy as np
import openslide
from PIL import Image, UnidentifiedImageError
from scipy import ndimage

from app.exceptions.errors import InvalidInputError

MEDIA_TYPE_JPEG = "image/jpeg"
MEDIA_TYPE_PNG = "image/png"
PIL_FORMAT_JPEG = "JPEG"
PIL_FORMAT_PNG = "PNG"
JPEG_QUALITY = 85
THUMBNAIL_SIZE = 512
DEFAULT_BACKGROUND_COLOR = "ffffff"
TISSUE_DETECTION_SIZE = 2048
TISSUE_WHITE_THRESHOLD = 220
TISSUE_MIN_COMPONENT_RATIO = 0.05
NOT_A_SLIDE = "'{filename}' is not a supported slide or image file."
IMAGE_TOO_LARGE = "'{filename}' is too large for a plain image; convert it to a pyramidal format (e.g. SVS)."


def open_slide_file(path: Path) -> openslide.AbstractSlide:
    """Open a whole-slide image with OpenSlide, or a plain image (JPEG, PNG, ...) as an ImageSlide.

    Plain images are decoded eagerly: PIL otherwise reads pixel data lazily from the file on first
    access, which corrupts reads when several threads share one handle (OpenSlide handles are safe).
    """
    try:
        return openslide.OpenSlide(path)
    except openslide.OpenSlideUnsupportedFormatError:
        pass
    except openslide.OpenSlideError as exc:
        raise InvalidInputError(NOT_A_SLIDE.format(filename=path.name)) from exc
    try:
        image = Image.open(path)
        image.load()
    except Image.DecompressionBombError as exc:
        raise InvalidInputError(IMAGE_TOO_LARGE.format(filename=path.name)) from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidInputError(NOT_A_SLIDE.format(filename=path.name)) from exc
    return openslide.ImageSlide(image)


def encode_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format=PIL_FORMAT_JPEG, quality=JPEG_QUALITY)
    return buffer.getvalue()


def encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=PIL_FORMAT_PNG)
    return buffer.getvalue()


def render_thumbnail(slide: openslide.AbstractSlide) -> bytes:
    return encode_jpeg(slide.get_thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE)))


def detect_tissue_bbox(slide: openslide.AbstractSlide) -> dict[str, int] | None:
    """Bounding box {x, y, width, height} (level-0 pixels) of the tissue, or None if there is none.

    Port of the prototype's extract_tissue.py: pixels darker than the white threshold in any channel
    are tissue; connected components smaller than a fraction of the largest one (dust) are ignored.
    """
    thumbnail = slide.get_thumbnail((TISSUE_DETECTION_SIZE, TISSUE_DETECTION_SIZE)).convert("RGB")
    mask = (np.asarray(thumbnail) < TISSUE_WHITE_THRESHOLD).any(axis=2)
    if not mask.any():
        return None
    labels, _ = ndimage.label(mask)
    sizes = np.bincount(labels.ravel())[1:]
    tissue = np.isin(labels, np.flatnonzero(sizes >= sizes.max() * TISSUE_MIN_COMPONENT_RATIO) + 1)
    rows = np.flatnonzero(tissue.any(axis=1))
    cols = np.flatnonzero(tissue.any(axis=0))

    width, height = slide.dimensions
    scale_x, scale_y = width / thumbnail.width, height / thumbnail.height
    x0, y0 = math.floor(int(cols[0]) * scale_x), math.floor(int(rows[0]) * scale_y)
    x1 = min(width, math.ceil((int(cols[-1]) + 1) * scale_x))
    y1 = min(height, math.ceil((int(rows[-1]) + 1) * scale_y))
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def read_region_image(
    slide: openslide.AbstractSlide, x: int, y: int, width: int, height: int, max_size: int
) -> Image.Image:
    """Read a level-0 rectangle from the most suitable pyramid level, scaled to fit `max_size`."""
    downsample = max(width / max_size, height / max_size, 1.0)
    level = slide.get_best_level_for_downsample(downsample)
    level_downsample = slide.level_downsamples[level]
    region = slide.read_region(
        (x, y),
        level,
        (max(1, math.ceil(width / level_downsample)), max(1, math.ceil(height / level_downsample))),
    )
    # Transparent pixels are unscanned area: paint them in the slide's background colour.
    background = "#" + slide.properties.get(openslide.PROPERTY_NAME_BACKGROUND_COLOR, DEFAULT_BACKGROUND_COLOR)
    image = Image.new("RGB", region.size, background)
    image.paste(region, mask=region.getchannel("A"))
    target_size = (max(1, round(width / downsample)), max(1, round(height / downsample)))
    return image if image.size == target_size else image.resize(target_size, Image.Resampling.LANCZOS)
