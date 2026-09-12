import logging
import uuid

import openslide
from sqlalchemy.orm import Session, sessionmaker

from app.exceptions.errors import AppError
from app.models.slide import Slide, SlideStatus
from app.repositories.slides import SlideRepository
from app.services.slide_analysis import MEDIA_TYPE_JPEG, detect_tissue_bbox, open_slide_file, render_thumbnail
from app.services.slide_files import SlideFileService, find_slide_entry
from app.services.slides import THUMBNAIL_OBJECT_KEY
from app.services.storage import ObjectStorage

logger = logging.getLogger(__name__)

VENDOR_PLAIN_IMAGE = "image"
PROCESSING_FAILED = "Unexpected error while processing the slide."


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class SlideIngestService:
    """Turns an uploaded object into a ready slide: local copy, validation, metadata, thumbnail, tissue box.

    Runs as a background task, so it opens its own database session, and it always leaves the slide
    in a terminal state (ready or failed).
    """

    def __init__(
        self, session_factory: sessionmaker[Session], storage: ObjectStorage, files: SlideFileService
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._files = files

    def ingest(self, slide_id: uuid.UUID) -> None:
        with self._session_factory() as session:
            slides = SlideRepository(session)
            slide = slides.get(slide_id)
            if slide is None:
                logger.warning("Ingest skipped: slide %s does not exist", slide_id)
                return
            try:
                self._process(slide)
                slide.status, slide.error_message = SlideStatus.READY, None
            except AppError as exc:
                logger.warning("Slide %s failed processing: %s", slide_id, exc.message)
                slide.status, slide.error_message = SlideStatus.FAILED, exc.message
            except Exception:
                # A background job must never leave the slide stuck in "processing".
                logger.exception("Unexpected error while processing slide %s", slide_id)
                slide.status, slide.error_message = SlideStatus.FAILED, PROCESSING_FAILED
            slides.save(slide)

    def _process(self, slide: Slide) -> None:
        # All fallible work happens before the slide is modified.
        slide_dir = self._files.materialize(slide.id, slide.storage_key)
        with open_slide_file(find_slide_entry(slide_dir)) as handle:
            properties = dict(handle.properties)
            width, height = handle.dimensions
            level_count = handle.level_count
            thumbnail = render_thumbnail(handle)
            tissue_bbox = detect_tissue_bbox(handle)
        self._storage.put_bytes(THUMBNAIL_OBJECT_KEY.format(slide_id=slide.id), thumbnail, MEDIA_TYPE_JPEG)

        slide.vendor = properties.get(openslide.PROPERTY_NAME_VENDOR, VENDOR_PLAIN_IMAGE)
        slide.width, slide.height, slide.level_count = width, height, level_count
        slide.mpp_x = _to_float(properties.get(openslide.PROPERTY_NAME_MPP_X))
        slide.objective_power = _to_float(properties.get(openslide.PROPERTY_NAME_OBJECTIVE_POWER))
        # The Deep Zoom image starts at the scan bounds (DeepZoomGenerator with limit_bounds=True).
        slide.image_offset = {
            "x": int(properties.get(openslide.PROPERTY_NAME_BOUNDS_X, 0)),
            "y": int(properties.get(openslide.PROPERTY_NAME_BOUNDS_Y, 0)),
        }
        slide.tissue_bbox = tissue_bbox
        slide.properties = properties
