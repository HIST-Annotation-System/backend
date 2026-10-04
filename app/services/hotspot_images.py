"""Decoding, validating and encoding hotspot images (uploaded PNG/JPEG files and captured slide squares)."""

import io
import warnings
from dataclasses import dataclass

from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError

from app.exceptions.errors import InvalidInputError
from app.models.hotspot import HotspotImageType
from app.services.slide_analysis import (
    MEDIA_TYPE_JPEG,
    MEDIA_TYPE_PNG,
    PIL_FORMAT_JPEG,
    PIL_FORMAT_PNG,
    encode_jpeg,
    encode_png,
)

UPLOAD_MAX_BYTES = 100 * 1024**2
# Checked before decoding, so a small file can't expand into gigabytes of pixels.
UPLOAD_MAX_PIXELS = 100_000_000
# The annotation viewer shows a hotspot as one image (no tile pyramid): larger uploads are scaled down.
UPLOAD_MAX_SIDE = 8192
THUMBNAIL_SIZE = 256
THUMBNAIL_BACKGROUND = "#ffffff"
EXIF_UPRIGHT = 1

UPLOAD_TOO_LARGE = f"The file is larger than {UPLOAD_MAX_BYTES // 1024**2} MB."
UPLOAD_NOT_AN_IMAGE = "'{filename}' is not a PNG or JPEG image."
UPLOAD_TOO_MANY_PIXELS = f"'{{filename}}' has more than {UPLOAD_MAX_PIXELS:,} pixels."

FORMAT_TYPES = {PIL_FORMAT_PNG: HotspotImageType.PNG, PIL_FORMAT_JPEG: HotspotImageType.JPEG}
MEDIA_TYPES = {HotspotImageType.PNG: MEDIA_TYPE_PNG, HotspotImageType.JPEG: MEDIA_TYPE_JPEG}
EXTENSIONS = {HotspotImageType.PNG: "png", HotspotImageType.JPEG: "jpg"}


@dataclass(frozen=True)
class EncodedImage:
    data: bytes
    image_type: HotspotImageType
    width: int
    height: int
    thumbnail: bytes


def encode_captured(image: Image.Image) -> EncodedImage:
    """A square read from a slide, stored losslessly."""
    return EncodedImage(encode_png(image), HotspotImageType.PNG, image.width, image.height, render_thumbnail(image))


def decode_upload(data: bytes, filename: str) -> EncodedImage:
    """Validate an uploaded PNG/JPEG. The original bytes are kept unless the image must be rotated
    upright (EXIF orientation) or scaled down, so a JPEG is not recompressed for nothing.
    """
    if len(data) > UPLOAD_MAX_BYTES:
        raise InvalidInputError(UPLOAD_TOO_LARGE)
    try:
        with warnings.catch_warnings():
            # Pixel count is limited explicitly below; Pillow's own bomb check would only warn first.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                image_type = FORMAT_TYPES.get(source.format or "")
                if image_type is None:
                    raise InvalidInputError(UPLOAD_NOT_AN_IMAGE.format(filename=filename))
                if source.width * source.height > UPLOAD_MAX_PIXELS:
                    raise InvalidInputError(UPLOAD_TOO_MANY_PIXELS.format(filename=filename))
                upright = source.getexif().get(ExifTags.Base.Orientation, EXIF_UPRIGHT) == EXIF_UPRIGHT
                image = ImageOps.exif_transpose(source)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise InvalidInputError(UPLOAD_NOT_AN_IMAGE.format(filename=filename)) from exc

    oversized = max(image.size) > UPLOAD_MAX_SIDE
    if oversized:
        image.thumbnail((UPLOAD_MAX_SIDE, UPLOAD_MAX_SIDE), Image.Resampling.LANCZOS)
    if not upright or oversized:
        data = encode_png(image) if image_type == HotspotImageType.PNG else encode_jpeg(image)
    return EncodedImage(data, image_type, image.width, image.height, render_thumbnail(image))


def render_thumbnail(image: Image.Image) -> bytes:
    """A JPEG preview for the catalog; transparency is flattened onto white."""
    thumbnail = image.copy()
    thumbnail.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
    if thumbnail.mode in ("RGBA", "LA", "P"):
        rgba = thumbnail.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, THUMBNAIL_BACKGROUND)
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        thumbnail = flattened
    return encode_jpeg(thumbnail)
