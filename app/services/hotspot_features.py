"""Simple image descriptors of a hotspot, used to sort the catalog.

All of them are computed on the stored thumbnail (at most THUMBNAIL_SIZE per side), which is cheap and
keeps the values comparable between hotspots of very different image sizes.
"""

import io
import math
from typing import Final

import numpy as np
import numpy.typing as npt
from PIL import Image
from scipy import ndimage

from app.models.hotspot import HotspotFeature

GRAY_LEVELS: Final = 256
# Fewer gray levels keep the co-occurrence matrix dense enough on a small image.
GLCM_LEVELS: Final = 32
# Pixel offsets (dy, dx) the co-occurrence matrix is averaged over: right, down, both diagonals.
GLCM_OFFSETS: Final = ((0, 1), (1, 0), (1, 1), (1, -1))
# Weight of the mean opponent color against its spread (Hasler & Süsstrunk, 2003).
COLORFULNESS_MEAN_WEIGHT: Final = 0.3

type FloatArray = npt.NDArray[np.float64]


def compute_features(thumbnail: bytes) -> dict[str, float]:
    """Every HotspotFeature of a thumbnail (an RGB JPEG), keyed by the feature's value."""
    with Image.open(io.BytesIO(thumbnail)) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float64)
    gray = rgb @ np.array([0.299, 0.587, 0.114])
    return {
        HotspotFeature.ENTROPY.value: shannon_entropy(gray),
        HotspotFeature.GLCM_CONTRAST.value: glcm_contrast(gray),
        HotspotFeature.EDGE_DENSITY.value: edge_density(gray),
        HotspotFeature.COLORFULNESS.value: colorfulness(rgb),
    }


def shannon_entropy(gray: FloatArray) -> float:
    """Entropy of the gray-level histogram in bits (0 for a flat image, up to 8 for an even spread)."""
    counts = np.bincount(np.clip(gray, 0, GRAY_LEVELS - 1).astype(np.intp).ravel(), minlength=GRAY_LEVELS)
    probabilities = counts[counts > 0] / counts.sum()
    # max(): a single gray level gives -0.0.
    return max(0.0, float(-(probabilities * np.log2(probabilities)).sum()))


def glcm_contrast(gray: FloatArray) -> float:
    """Haralick contrast of the gray-level co-occurrence matrix: how much neighboring pixels differ.

    Gray levels are quantized to GLCM_LEVELS and the result is scaled back to 0..255 units squared, so
    it reads like a mean squared neighbor difference.
    """
    levels = np.clip(gray * GLCM_LEVELS / GRAY_LEVELS, 0, GLCM_LEVELS - 1).astype(np.intp)
    height, width = levels.shape
    contrasts = []
    for dy, dx in GLCM_OFFSETS:
        rows = slice(0, height - dy)
        first_cols = slice(max(0, -dx), width - max(0, dx))
        second_cols = slice(max(0, dx), width - max(0, -dx))
        first = levels[rows, first_cols]
        second = levels[dy:, second_cols]
        if first.size == 0:
            continue
        pairs = np.bincount((first * GLCM_LEVELS + second).ravel(), minlength=GLCM_LEVELS**2)
        matrix = pairs.reshape(GLCM_LEVELS, GLCM_LEVELS).astype(np.float64)
        # Symmetric: a pair counts in both directions.
        matrix += matrix.T
        matrix /= matrix.sum()
        i, j = np.indices(matrix.shape)
        contrasts.append(float((matrix * (i - j) ** 2).sum()))
    if not contrasts:
        return 0.0
    scale = GRAY_LEVELS / GLCM_LEVELS
    return float(np.mean(contrasts)) * scale**2


def edge_density(gray: FloatArray) -> float:
    """Mean Sobel gradient magnitude: high for fine, sharp structure, low for smooth or blurred areas."""
    magnitude = np.hypot(ndimage.sobel(gray, axis=0), ndimage.sobel(gray, axis=1))
    return float(magnitude.mean())


def colorfulness(rgb: FloatArray) -> float:
    """Hasler & Süsstrunk colorfulness: spread and strength of the red-green and yellow-blue opponents."""
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    red_green = red - green
    yellow_blue = (red + green) / 2 - blue
    spread = math.hypot(float(red_green.std()), float(yellow_blue.std()))
    mean = math.hypot(float(red_green.mean()), float(yellow_blue.mean()))
    return spread + COLORFULNESS_MEAN_WEIGHT * mean
