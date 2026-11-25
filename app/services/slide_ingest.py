import logging
import math
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import openslide
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from app.exceptions.errors import AppError, InvalidInputError
from app.models.slide import Slide, SlideStatus
from app.repositories.slides import SlideRepository
from app.services.slide_analysis import (
    MEDIA_TYPE_JPEG,
    NOT_A_SLIDE,
    detect_tissue_bbox,
    open_slide_file,
    render_thumbnail,
)
from app.services.slide_files import SlideFileService, find_slide_entry
from app.services.slide_formats import FORMATS_BY_SUFFIX, PYRAMID_REQUIRED_ABOVE_PX
from app.services.slides import THUMBNAIL_OBJECT_KEY
from app.services.storage import ObjectStorage

logger = logging.getLogger(__name__)

PROCESSING_FAILED = "Unexpected error while processing the slide."
INGEST_INTERRUPTED = "Ingestion was interrupted; upload the slide again."
# Every teammate runs an API against the same database, so at startup only slides stuck far longer
# than any real ingest are failed - never one another instance is still processing right now.
INGEST_STALE_AFTER = timedelta(hours=3)
# Progress is committed at most every few percent: it is only a progress bar.
INGEST_PROGRESS_STEP = 5
VENDOR_MISMATCH = "'{filename}' contains {vendor} data, which doesn't match its extension."
MISSING_MPP = "The slide has no resolution (MPP) metadata."
NOT_PYRAMIDAL = "The slide is not pyramidal (e.g. a flat or OME-TIFF). Convert it to a pyramidal tiled TIFF or SVS."


class SlideDeletedError(Exception):
    """The slide was deleted (by another request) while it was being ingested."""


# What touching a slide row that another request deleted raises, depending on the moment it happens.
SLIDE_DELETED_ERRORS = (SlideDeletedError, StaleDataError, ObjectDeletedError)


def fail_interrupted_ingests(session_factory: sessionmaker[Session], now: datetime | None = None) -> None:
    """Fails slides left in `processing` by a crashed process; ingestion is in-process, so they can't resume.

    Only slides older than `INGEST_STALE_AFTER`: younger ones may be in progress on another instance.
    """
    cutoff = (now or datetime.now(UTC)) - INGEST_STALE_AFTER
    with session_factory() as session:
        interrupted = session.scalars(
            select(Slide).where(Slide.status == SlideStatus.PROCESSING, Slide.created_at < cutoff)
        ).all()
        for slide in interrupted:
            slide.status, slide.error_message, slide.ingest_progress = SlideStatus.FAILED, INGEST_INTERRUPTED, None
        session.commit()
    if interrupted:
        logger.warning("Marked %d interrupted slide(s) as failed", len(interrupted))


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _require_matching_vendor(entry: Path) -> None:
    """The file's content must be what its extension claims (e.g. a generic TIFF renamed .svs is rejected)."""
    vendor = openslide.OpenSlide.detect_format(entry)
    slide_format = FORMATS_BY_SUFFIX.get(entry.suffix.lower())
    if vendor is None or slide_format is None:
        raise InvalidInputError(NOT_A_SLIDE.format(filename=entry.name))
    if vendor not in slide_format.vendors:
        raise InvalidInputError(VENDOR_MISMATCH.format(filename=entry.name, vendor=vendor))


def _require_usable_slide(properties: dict[str, str], dimensions: tuple[int, int], level_count: int) -> None:
    for key in (openslide.PROPERTY_NAME_MPP_X, openslide.PROPERTY_NAME_MPP_Y):
        mpp = _to_float(properties.get(key))
        if mpp is None or not math.isfinite(mpp) or mpp <= 0:
            raise InvalidInputError(MISSING_MPP)
    if max(dimensions) > PYRAMID_REQUIRED_ABOVE_PX and level_count == 1:
        raise InvalidInputError(NOT_PYRAMIDAL)


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
                self._ingest(slide, slides)
            except SLIDE_DELETED_ERRORS:
                session.rollback()
                self._discard_outputs(slide_id)
                logger.info("Slide %s was deleted during ingestion; its outputs were discarded", slide_id)

    def _ingest(self, slide: Slide, slides: SlideRepository) -> None:
        try:
            thumbnail = self._process(slide, slides)
            # Don't recreate objects under the prefix of a slide someone deleted meanwhile.
            if not slides.exists(slide.id):
                raise SlideDeletedError
            self._storage.put_bytes(THUMBNAIL_OBJECT_KEY.format(slide_id=slide.id), thumbnail, MEDIA_TYPE_JPEG)
            slide.status, slide.error_message = SlideStatus.READY, None
        except SLIDE_DELETED_ERRORS:
            raise
        except AppError as exc:
            logger.warning("Slide %s failed processing: %s", slide.id, exc.message)
            slide.status, slide.error_message = SlideStatus.FAILED, exc.message
        except Exception:
            # A background job must never leave the slide stuck in "processing".
            logger.exception("Unexpected error while processing slide %s", slide.id)
            slide.status, slide.error_message = SlideStatus.FAILED, PROCESSING_FAILED
        slide.ingest_progress = None
        slides.save(slide)

    def _discard_outputs(self, slide_id: uuid.UUID) -> None:
        """Removes what ingestion may have produced after the slide's own deletion cleaned up."""
        self._files.purge(slide_id)
        self._storage.delete_objects([THUMBNAIL_OBJECT_KEY.format(slide_id=slide_id)])

    def _process(self, slide: Slide, slides: SlideRepository) -> bytes:
        """Validates and analyses the slide, filling in its metadata; returns its thumbnail."""
        # All fallible work happens before the slide is modified.
        slide_dir = self._files.materialize(slide.id, slide.storage_key, self._download_progress(slide, slides))
        entry = find_slide_entry(slide_dir)
        _require_matching_vendor(entry)
        with open_slide_file(entry) as handle:
            properties = dict(handle.properties)
            width, height = handle.dimensions
            level_count = handle.level_count
            _require_usable_slide(properties, (width, height), level_count)
            tissue_bbox = detect_tissue_bbox(handle)
            thumbnail = render_thumbnail(handle, tissue_bbox)

        slide.vendor = properties.get(openslide.PROPERTY_NAME_VENDOR)
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
        return thumbnail

    def _download_progress(self, slide: Slide, slides: SlideRepository) -> Callable[[int], None]:
        """A boto3 download Callback that commits `slide.ingest_progress` as bytes come in.

        boto3 may call this from several worker threads at once for a multipart download; the lock
        serializes their commits onto the one session `slides` shares with the rest of `_process`,
        rather than allowing genuinely concurrent access to it (SQLAlchemy sessions aren't thread-safe).
        Capped at 99 so the bar never reads "100%" while thumbnail/tissue analysis is still running.
        """
        total = slide.file_size_bytes or 0
        state = {"downloaded": 0, "last_reported": -INGEST_PROGRESS_STEP}
        lock = threading.Lock()

        def on_progress(chunk_bytes: int) -> None:
            if total <= 0:
                return
            with lock:
                state["downloaded"] += chunk_bytes
                percent = min(99, int(state["downloaded"] * 100 / total))
                if percent >= state["last_reported"] + INGEST_PROGRESS_STEP:
                    state["last_reported"] = percent
                    slide.ingest_progress = percent
                    try:
                        slides.save(slide)
                    except SLIDE_DELETED_ERRORS:
                        # Stop downloading a file nobody needs any more (the transfer re-raises it).
                        slides.rollback()
                        raise SlideDeletedError from None

        return on_progress
