import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import PurePath

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.constants import NAME_MAX_LENGTH
from app.exceptions.errors import AppError, ConflictError, InvalidInputError, NotFoundError, StorageError
from app.models.hotspot import Hotspot, HotspotBatch, HotspotBatchStatus, HotspotFeature, HotspotFolder
from app.models.slide import Slide
from app.repositories.hotspots import HotspotRepository
from app.repositories.slides import SlideRepository
from app.schemas.hotspots import (
    HotspotBatchCreate,
    HotspotBatchRead,
    HotspotFolderRead,
    HotspotRead,
    HotspotSort,
    HotspotUpdate,
    SortOrder,
)
from app.services.hotspot_features import compute_features
from app.services.hotspot_images import EXTENSIONS, MEDIA_TYPES, EncodedImage, decode_upload, encode_captured
from app.services.slide_analysis import MEDIA_TYPE_JPEG
from app.services.slides import SlideService, require_slide
from app.services.storage import ObjectStorage

# Outside "slides/": a hotspot keeps its images when its source slide is deleted.
HOTSPOT_OBJECTS_PREFIX = "hotspots/{hotspot_id}/"
HOTSPOT_IMAGE_KEY = HOTSPOT_OBJECTS_PREFIX + "image.{extension}"
HOTSPOT_THUMBNAIL_KEY = HOTSPOT_OBJECTS_PREFIX + "thumbnail.jpg"
# Side of every hotspot cut from a slide, in level-0 pixels (stored at full resolution).
HOTSPOT_BOX_SIZE = 1024
UPLOADED_IMAGE_DEFAULT_NAME = "Obraz"
FOLDER_NOT_FOUND = "Hotspot folder {folder_id} does not exist."
FOLDER_NAME_TAKEN = "A hotspot folder named '{name}' already exists."
HOTSPOT_NOT_FOUND = "Hotspot {hotspot_id} does not exist."
BATCH_NOT_FOUND = "Hotspot batch {batch_id} does not exist."
BOX_OUTSIDE_SLIDE = "The box at ({x}, {y}) does not lie wholly inside the slide."
BATCH_FAILED = "Unexpected error while cutting out the hotspots."
BATCH_INTERRUPTED = "Cutting out the hotspots was interrupted; select the remaining boxes again."
# Batches run in-process, so one still running long after it started belongs to a crashed process.
# Every teammate runs an API against the same database: only batches far older than any real one are failed.
BATCH_STALE_AFTER = timedelta(hours=1)
# Failed batches stay listed this long, so the user can see what went wrong.
FAILED_BATCH_LISTED_FOR = timedelta(hours=24)

logger = logging.getLogger(__name__)


def require_hotspot(hotspots: HotspotRepository, hotspot_id: uuid.UUID) -> Hotspot:
    hotspot = hotspots.get(hotspot_id)
    if hotspot is None:
        raise NotFoundError(HOTSPOT_NOT_FOUND.format(hotspot_id=hotspot_id))
    return hotspot


def fail_interrupted_batches(session_factory: sessionmaker[Session], now: datetime | None = None) -> None:
    """Fails batches left running by a crashed process (see `BATCH_STALE_AFTER`)."""
    cutoff = (now or datetime.now(UTC)) - BATCH_STALE_AFTER
    with session_factory() as session:
        hotspots = HotspotRepository(session)
        interrupted = hotspots.list_running_batches(created_before=cutoff)
        for batch in interrupted:
            batch.status, batch.error_message = HotspotBatchStatus.FAILED, BATCH_INTERRUPTED
        hotspots.save_all(interrupted)
    if interrupted:
        logger.warning("Marked %d interrupted hotspot batch(es) as failed", len(interrupted))


class HotspotService:
    """The hotspot catalog: folders of square slide snapshots, the snapshot images themselves, and the
    batches that cut them out of slides in the background."""

    def __init__(
        self,
        hotspots: HotspotRepository,
        slides: SlideRepository,
        slide_service: SlideService,
        storage: ObjectStorage,
    ) -> None:
        self._hotspots = hotspots
        self._slides = slides
        self._slide_service = slide_service
        self._storage = storage

    def list_folders(self) -> list[HotspotFolderRead]:
        return [
            self._folder_read(folder, count) for folder, count in self._hotspots.list_folders_with_counts()
        ]

    def create_folder(self, name: str) -> HotspotFolderRead:
        self._require_free_name(name)
        folder = self._save_folder(HotspotFolder(name=name))
        return self._folder_read(folder, 0)

    def rename_folder(self, folder_id: uuid.UUID, name: str) -> HotspotFolderRead:
        folder = self._require_folder(folder_id)
        if name != folder.name:
            self._require_free_name(name)
            folder.name = name
            folder = self._save_folder(folder)
        return self._folder_read(folder, self._hotspots.count_in_folder(folder_id))

    def delete_folder(self, folder_id: uuid.UUID) -> None:
        folder = self._require_folder(folder_id)
        hotspot_ids = [hotspot.id for hotspot in self._hotspots.list_hotspots(folder_id)]
        self._hotspots.delete(folder)
        for hotspot_id in hotspot_ids:
            self._storage.delete_prefix(HOTSPOT_OBJECTS_PREFIX.format(hotspot_id=hotspot_id))

    def list_hotspots(
        self,
        folder_id: uuid.UUID | None,
        sort: HotspotSort = HotspotSort.CREATED_AT,
        order: SortOrder = SortOrder.DESC,
        slide_id: uuid.UUID | None = None,
    ) -> list[HotspotRead]:
        """Hotspots ordered by creation time or by an image feature (ties and missing values: newest first;
        hotspots whose feature could not be computed always come last).
        """
        hotspots = list(self._hotspots.list_hotspots(folder_id, slide_id))  # newest first
        self._fill_missing_features(hotspots)
        if sort != HotspotSort.CREATED_AT:
            known = [hotspot for hotspot in hotspots if hotspot.features and sort.value in hotspot.features]
            unknown = [hotspot for hotspot in hotspots if not (hotspot.features and sort.value in hotspot.features)]
            # A stable sort keeps the newest-first order among equal values.
            known.sort(key=lambda hotspot: (hotspot.features or {})[sort.value], reverse=order == SortOrder.DESC)
            hotspots = known + unknown
        elif order == SortOrder.ASC:
            hotspots.reverse()
        return [HotspotRead.model_validate(item) for item in hotspots]

    def get_hotspot(self, hotspot_id: uuid.UUID) -> HotspotRead:
        return HotspotRead.model_validate(require_hotspot(self._hotspots, hotspot_id))

    def create_batch(self, data: HotspotBatchCreate) -> tuple[HotspotBatchRead, bool]:
        """The batch, and whether it is new (so it still has to be run); a retried request gets the existing one."""
        existing = self._hotspots.get_batch(data.id)
        if existing is not None:
            return self._batch_read(existing), False
        slide = require_slide(self._slides, data.slide_id, ready=True)
        self._require_folder(data.folder_id)
        for x, y in data.boxes:
            if x + HOTSPOT_BOX_SIZE > (slide.width or 0) or y + HOTSPOT_BOX_SIZE > (slide.height or 0):
                raise InvalidInputError(BOX_OUTSIDE_SLIDE.format(x=x, y=y))
        batch = HotspotBatch(
            id=data.id,
            slide_id=slide.id,
            slide_name=slide.name,
            folder_id=data.folder_id,
            name_prefix=data.name_prefix,
            boxes=[[x, y] for x, y in data.boxes],
            done=0,
            status=HotspotBatchStatus.RUNNING,
        )
        try:
            return self._batch_read(self._hotspots.save(batch)), True
        except IntegrityError:
            # A concurrent retry created it first.
            return self.get_batch(data.id), False

    def run_batch(self, batch_id: uuid.UUID) -> None:
        """Cut out the batch's boxes one by one. Each hotspot is saved on its own, so finished ones stay when a
        later box fails; the batch always ends done or failed. Runs in the background.
        """
        batch = self._hotspots.get_batch(batch_id)
        if batch is None or batch.status != HotspotBatchStatus.RUNNING:
            return
        try:
            slide = require_slide(self._slides, batch.slide_id, ready=True)
            for x, y in batch.boxes[batch.done :]:
                self._capture(slide, batch.folder_id, f"{batch.name_prefix} ({x}, {y})"[:NAME_MAX_LENGTH], x, y)
                batch.done += 1
                self._hotspots.save(batch)
            batch.status = HotspotBatchStatus.DONE
        except AppError as exc:
            logger.warning("Hotspot batch %s failed: %s", batch_id, exc.message)
            batch.status, batch.error_message = HotspotBatchStatus.FAILED, exc.message
        except Exception:
            logger.exception("Unexpected error in hotspot batch %s", batch_id)
            batch.status, batch.error_message = HotspotBatchStatus.FAILED, BATCH_FAILED
        try:
            self._hotspots.save(batch)
        except Exception:
            # E.g. its slide or folder was deleted meanwhile, taking the batch with it.
            logger.warning("Could not record the end of hotspot batch %s", batch_id, exc_info=True)

    def list_batches(self) -> list[HotspotBatchRead]:
        """Running batches, and recently failed ones."""
        failed_since = datetime.now(UTC) - FAILED_BATCH_LISTED_FOR
        return [self._batch_read(batch) for batch in self._hotspots.list_batches(failed_since)]

    def get_batch(self, batch_id: uuid.UUID) -> HotspotBatchRead:
        batch = self._hotspots.get_batch(batch_id)
        if batch is None:
            raise NotFoundError(BATCH_NOT_FOUND.format(batch_id=batch_id))
        return self._batch_read(batch)

    def upload_image(self, folder_id: uuid.UUID, filename: str, data: bytes) -> HotspotRead:
        """A hotspot from an uploaded PNG/JPEG, named after the file."""
        self._require_folder(folder_id)
        image = decode_upload(data, filename)
        name = PurePath(filename).stem.strip()[:NAME_MAX_LENGTH] or UPLOADED_IMAGE_DEFAULT_NAME
        return self._store(Hotspot(id=uuid.uuid4(), folder_id=folder_id, name=name), image)

    def update_hotspot(self, hotspot_id: uuid.UUID, data: HotspotUpdate) -> HotspotRead:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        if data.folder_id is not None:
            self._require_folder(data.folder_id)
            hotspot.folder_id = data.folder_id
        if data.name is not None:
            hotspot.name = data.name
        return HotspotRead.model_validate(self._hotspots.save(hotspot))

    def delete_hotspot(self, hotspot_id: uuid.UUID) -> None:
        self._hotspots.delete(require_hotspot(self._hotspots, hotspot_id))
        self._storage.delete_prefix(HOTSPOT_OBJECTS_PREFIX.format(hotspot_id=hotspot_id))

    def get_image(self, hotspot_id: uuid.UUID) -> tuple[bytes, str]:
        """The image bytes and their media type."""
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        key = HOTSPOT_IMAGE_KEY.format(hotspot_id=hotspot_id, extension=EXTENSIONS[hotspot.image_type])
        return self._storage.get_bytes(key), MEDIA_TYPES[hotspot.image_type]

    def get_thumbnail(self, hotspot_id: uuid.UUID) -> bytes:
        require_hotspot(self._hotspots, hotspot_id)
        return self._storage.get_bytes(HOTSPOT_THUMBNAIL_KEY.format(hotspot_id=hotspot_id))

    def _fill_missing_features(self, hotspots: list[Hotspot]) -> None:
        """Compute the features of hotspots stored before they existed (or before a feature was added)."""
        expected = set(HotspotFeature)
        changed = []
        for hotspot in hotspots:
            if hotspot.features is not None and expected <= hotspot.features.keys():
                continue
            try:
                thumbnail = self._storage.get_bytes(HOTSPOT_THUMBNAIL_KEY.format(hotspot_id=hotspot.id))
                hotspot.features = compute_features(thumbnail)
            except (StorageError, OSError, ValueError):
                # An unreadable thumbnail only leaves this hotspot unsorted; it is retried on the next listing.
                logger.warning("Could not compute features of hotspot %s", hotspot.id, exc_info=True)
                continue
            changed.append(hotspot)
        if changed:
            self._hotspots.save_all(changed)

    def _capture(self, slide: Slide, folder_id: uuid.UUID, name: str, x: int, y: int) -> HotspotRead:
        """A full-resolution `HOTSPOT_BOX_SIZE` square of the slide becomes a hotspot."""
        image = encode_captured(self._slide_service.read_square(slide.id, x, y, HOTSPOT_BOX_SIZE, HOTSPOT_BOX_SIZE))
        hotspot = Hotspot(
            id=uuid.uuid4(),
            folder_id=folder_id,
            name=name,
            slide_id=slide.id,
            slide_name=slide.name,
            x=x,
            y=y,
            size=HOTSPOT_BOX_SIZE,
            mpp=slide.mpp_x,
        )
        return self._store(hotspot, image)

    def _store(self, hotspot: Hotspot, image: EncodedImage) -> HotspotRead:
        """Upload the images, then save the row (dropping the images again if that fails)."""
        hotspot.image_width, hotspot.image_height, hotspot.image_type = image.width, image.height, image.image_type
        hotspot.features = compute_features(image.thumbnail)
        image_key = HOTSPOT_IMAGE_KEY.format(hotspot_id=hotspot.id, extension=EXTENSIONS[image.image_type])
        self._storage.put_bytes(image_key, image.data, MEDIA_TYPES[image.image_type])
        self._storage.put_bytes(HOTSPOT_THUMBNAIL_KEY.format(hotspot_id=hotspot.id), image.thumbnail, MEDIA_TYPE_JPEG)
        try:
            return HotspotRead.model_validate(self._hotspots.save(hotspot))
        except Exception:
            self._storage.delete_prefix(HOTSPOT_OBJECTS_PREFIX.format(hotspot_id=hotspot.id))
            raise

    def _batch_read(self, batch: HotspotBatch) -> HotspotBatchRead:
        return HotspotBatchRead(
            id=batch.id,
            slide_id=batch.slide_id,
            slide_name=batch.slide_name,
            folder_id=batch.folder_id,
            folder_name=batch.folder.name,
            total=len(batch.boxes),
            done=batch.done,
            status=batch.status,
            error_message=batch.error_message,
            created_at=batch.created_at,
        )

    def _folder_read(self, folder: HotspotFolder, hotspot_count: int) -> HotspotFolderRead:
        return HotspotFolderRead(
            id=folder.id, name=folder.name, hotspot_count=hotspot_count, created_at=folder.created_at
        )

    def _require_folder(self, folder_id: uuid.UUID) -> HotspotFolder:
        folder = self._hotspots.get_folder(folder_id)
        if folder is None:
            raise NotFoundError(FOLDER_NOT_FOUND.format(folder_id=folder_id))
        return folder

    def _require_free_name(self, name: str) -> None:
        if self._hotspots.get_folder_by_name(name) is not None:
            raise ConflictError(FOLDER_NAME_TAKEN.format(name=name))

    def _save_folder(self, folder: HotspotFolder) -> HotspotFolder:
        # The unique constraint still guards against a concurrent request taking the same name.
        try:
            return self._hotspots.save(folder)
        except IntegrityError as exc:
            raise ConflictError(FOLDER_NAME_TAKEN.format(name=folder.name)) from exc
