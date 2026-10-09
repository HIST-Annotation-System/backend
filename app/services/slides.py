import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass

import openslide
from openslide.deepzoom import DeepZoomGenerator
from PIL import Image

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError, StorageError
from app.models.slide import Slide, SlideStatus
from app.repositories.slides import SlideRepository
from app.schemas.slides import REGION_MAX_SIZE, RegionQuery, SlideDetail, SlideRead, slide_version
from app.services.slide_analysis import (
    MEDIA_TYPE_JPEG,
    encode_jpeg,
    encode_png,
    open_slide_file,
    read_region_image,
    render_thumbnail,
)
from app.services.slide_files import SlideFileService
from app.services.storage import ObjectStorage

# "_square": distinct from the old "thumbnail.jpg" key so writing it is always a fresh object,
# never an overwrite - some buckets have a retention policy that silently no-ops overwrites/deletes
# of existing objects (write succeeds, content doesn't change) rather than erroring.
THUMBNAIL_OBJECT_KEY = "slides/{slide_id}/thumbnail_square.jpg"
# Every object the application stores for a slide lives under its prefix, so deleting it cleans up everything.
SLIDE_OBJECTS_ROOT = "slides/"
SLIDE_OBJECTS_PREFIX = SLIDE_OBJECTS_ROOT + "{slide_id}/"
DZI_FORMAT = "jpeg"
DZI_TILE_SIZE = 254
DZI_OVERLAP = 1
SLIDE_HANDLE_CACHE_SIZE = 16
SLIDE_NOT_FOUND = "Slide {slide_id} does not exist."
SLIDE_MODIFIED = "Slide {slide_id} was changed by someone else in the meantime (it is now named '{name}')."
SLIDE_NOT_FOUND_CODE = "slide_not_found"
SLIDE_MODIFIED_CODE = "slide_modified"
SLIDE_NOT_READY = "Slide {slide_id} is not ready (status: {status})."
TILE_NOT_FOUND = "Tile {level}/{col}_{row} does not exist."
REGION_OUTSIDE_SLIDE = "The requested region lies outside the slide."
SQUARE_OUTSIDE_SLIDE = "The square must lie wholly inside the slide."


@dataclass(frozen=True)
class OpenedSlide:
    slide: openslide.AbstractSlide
    deepzoom: DeepZoomGenerator


class SlideHandleCache:
    """Bounded LRU cache of opened slides keyed by the immutable slide id.

    A resource cache, not application state: a miss simply re-opens the slide. Evicted entries are
    only dereferenced, never closed - OpenSlide forbids closing a handle while another thread reads
    it, and openslide-python closes it once the last request lets go of it.
    """

    def __init__(self, max_size: int = SLIDE_HANDLE_CACHE_SIZE) -> None:
        self._max_size = max_size
        self._entries: OrderedDict[uuid.UUID, OpenedSlide] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, slide_id: uuid.UUID) -> OpenedSlide | None:
        with self._lock:
            entry = self._entries.get(slide_id)
            if entry is not None:
                self._entries.move_to_end(slide_id)
            return entry

    def put(self, slide_id: uuid.UUID, entry: OpenedSlide) -> None:
        with self._lock:
            self._entries[slide_id] = entry
            self._entries.move_to_end(slide_id)
            while len(self._entries) > self._max_size:
                self._entries.popitem(last=False)

    def evict(self, slide_id: uuid.UUID) -> None:
        with self._lock:
            self._entries.pop(slide_id, None)


def require_slide(
    slides: SlideRepository, slide_id: uuid.UUID, *, ready: bool = False, for_update: bool = False
) -> Slide:
    """The slide or 404; `for_update` row-locks it until the next commit (see `get_for_update`)."""
    slide = slides.get_for_update(slide_id) if for_update else slides.get(slide_id)
    if slide is None:
        raise NotFoundError(SLIDE_NOT_FOUND.format(slide_id=slide_id), SLIDE_NOT_FOUND_CODE)
    if ready and slide.status != SlideStatus.READY:
        raise ConflictError(SLIDE_NOT_READY.format(slide_id=slide_id, status=slide.status))
    return slide


class SlideService:
    """Slide metadata, thumbnails, Deep Zoom tiles and region crops."""

    def __init__(
        self,
        slides: SlideRepository,
        storage: ObjectStorage,
        files: SlideFileService,
        handles: SlideHandleCache,
    ) -> None:
        self._slides = slides
        self._storage = storage
        self._files = files
        self._handles = handles

    def list_slides(self) -> list[SlideRead]:
        return [SlideRead.model_validate(slide) for slide in self._slides.list_all()]

    def get_slide(self, slide_id: uuid.UUID) -> SlideDetail:
        return SlideDetail.model_validate(require_slide(self._slides, slide_id))

    def rename_slide(self, slide_id: uuid.UUID, name: str, expected_version: str | None = None) -> SlideRead:
        slide = self._lock_unchanged(slide_id, expected_version)
        slide.name = name
        return SlideRead.model_validate(self._slides.save(slide))

    def delete_slide(self, slide_id: uuid.UUID, expected_version: str | None = None) -> None:
        slide = self._lock_unchanged(slide_id, expected_version)
        self._handles.evict(slide_id)
        self._files.purge(slide_id)
        slide_prefix = SLIDE_OBJECTS_PREFIX.format(slide_id=slide_id)
        if slide.status == SlideStatus.UPLOADING:
            # An interrupted upload's parts are invisible to listings but still stored (and billed).
            self._storage.abort_multipart_uploads(slide.storage_key)
        self._storage.delete_prefix(slide_prefix)
        # Imported slides keep their objects outside the prefix (imports never overlap another slide's).
        if not slide.storage_key.startswith(slide_prefix):
            if slide.storage_key.endswith("/"):
                self._storage.delete_prefix(slide.storage_key)
            else:
                self._storage.delete_objects([slide.storage_key])
        self._slides.delete(slide)

    def get_thumbnail(self, slide_id: uuid.UUID) -> bytes:
        slide = require_slide(self._slides, slide_id, ready=True)
        key = THUMBNAIL_OBJECT_KEY.format(slide_id=slide_id)
        try:
            return self._storage.get_bytes(key)
        except StorageError:
            # The stored thumbnail can go missing independently of the slide (bucket reset, object
            # expiry, a failed re-upload) even though the slide itself is still ready. Rebuild it from
            # the local slide file - already required to be present for tiles - instead of leaving the
            # slide permanently thumbnail-less.
            thumbnail = render_thumbnail(self._open(slide_id).slide, slide.tissue_bbox)
            self._storage.put_bytes(key, thumbnail, MEDIA_TYPE_JPEG)
            return thumbnail

    def get_dzi(self, slide_id: uuid.UUID) -> str:
        return self._open(slide_id).deepzoom.get_dzi(DZI_FORMAT)

    def get_tile(self, slide_id: uuid.UUID, level: int, col: int, row: int) -> bytes:
        deepzoom = self._open(slide_id).deepzoom
        if not 0 <= level < deepzoom.level_count:
            raise NotFoundError(TILE_NOT_FOUND.format(level=level, col=col, row=row))
        cols, rows = deepzoom.level_tiles[level]
        if not (0 <= col < cols and 0 <= row < rows):
            raise NotFoundError(TILE_NOT_FOUND.format(level=level, col=col, row=row))
        return encode_jpeg(deepzoom.get_tile(level, (col, row)))

    def export_region(self, slide_id: uuid.UUID, query: RegionQuery) -> bytes:
        slide = self._open(slide_id).slide
        slide_width, slide_height = slide.dimensions
        width = min(query.width, slide_width - query.x)
        height = min(query.height, slide_height - query.y)
        if width <= 0 or height <= 0:
            raise InvalidInputError(REGION_OUTSIDE_SLIDE)
        max_size = min(query.max_size, REGION_MAX_SIZE)
        return encode_png(read_region_image(slide, query.x, query.y, width, height, max_size))

    def read_square(self, slide_id: uuid.UUID, x: int, y: int, size: int, max_size: int) -> Image.Image:
        """A level-0 square lying wholly inside the slide, scaled so its side is at most `max_size` pixels."""
        slide = self._open(slide_id).slide
        slide_width, slide_height = slide.dimensions
        if x + size > slide_width or y + size > slide_height:
            raise InvalidInputError(SQUARE_OUTSIDE_SLIDE)
        return read_region_image(slide, x, y, size, size, max_size)

    def _lock_unchanged(self, slide_id: uuid.UUID, expected_version: str | None) -> Slide:
        """The slide, row-locked, provided it still is the version the client last saw (if it sent one)."""
        slide = require_slide(self._slides, slide_id, for_update=True)
        if expected_version is not None and expected_version != slide_version(slide.name):
            raise ConflictError(SLIDE_MODIFIED.format(slide_id=slide_id, name=slide.name), SLIDE_MODIFIED_CODE)
        return slide

    def _open(self, slide_id: uuid.UUID) -> OpenedSlide:
        # Cache hits never touch the database (SQLAlchemy sessions connect lazily).
        opened = self._handles.get(slide_id)
        if opened is None:
            handle = open_slide_file(self._files.entry_path(require_slide(self._slides, slide_id, ready=True)))
            deepzoom = DeepZoomGenerator(handle, tile_size=DZI_TILE_SIZE, overlap=DZI_OVERLAP, limit_bounds=True)
            opened = OpenedSlide(slide=handle, deepzoom=deepzoom)
            self._handles.put(slide_id, opened)
        return opened
