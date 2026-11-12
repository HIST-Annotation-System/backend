"""The backend's half of the cell segmentation pipeline; the model itself runs in the separate ml-worker repository.

Nothing here runs in a request. A new hotspot is simply saved as `pending`, and a background thread in each API that
has segmentation enabled sends pending hotspots to the worker. So a missing worker, or a broker that is down, can
never slow down or fail an upload: hotspots wait, and go out once the worker stack is back.

Nothing checks the contract below across the two repositories: a change here must be made in
`ml-worker/segmentation/contract.py` too, and `SCHEMA_VERSION` raised when the format changes.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.exceptions.errors import AppError
from app.models.cell_type import CELL_TYPE_NAME_MAX_LENGTH, UNCLASSIFIED_CELL_TYPE_NAME
from app.models.hotspot import AnnotationKind, AnnotationSource, Hotspot, HotspotAnnotation, SegmentationStatus
from app.repositories.cell_types import CellTypeRepository
from app.repositories.hotspots import HotspotRepository
from app.schemas.annotations import PolygonGeometry
from app.services.annotations import fit_to_bounds
from app.services.hotspot_images import EXTENSIONS
from app.services.hotspots import HOTSPOT_IMAGE_KEY, HOTSPOT_OBJECTS_PREFIX
from app.services.storage import ObjectStorage

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
JOBS_QUEUE = "segmentation-jobs"
RESULTS_QUEUE = "segmentation-results"
TASK_RUN = "segmentation.run"
TASK_SAVE_RESULT = "segmentation.save_result"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# Every stack sees the same database, so a claimed hotspot is left alone this long. A job also expires after this
# long, so while a worker is down each hotspot has at most one live job per stack, not one per resend.
SEGMENTATION_RESEND_AFTER = timedelta(minutes=30)
DISPATCH_INTERVAL_SECONDS = 5.0
# How long to leave a broker or database alone after a failure.
DISPATCH_RETRY_SECONDS = 30.0
DISPATCH_BATCH_SIZE = 20

RESULT_UNREADABLE = "The model's result could not be read."
UNSUPPORTED_SCHEMA = "The backend does not support result format {schema_version}; update the backend or ml-worker."

# The hotspot, its image's key, and the image's microns per pixel when known.
type PublishJob = Callable[[uuid.UUID, str, float | None], None]


class Cell(BaseModel):
    # An open ring of [x, y] in image pixels.
    ring: list[tuple[float, float]]
    # The model's name for the cell's type; null when it gave none.
    cell_type: str | None


class SegmentationResult(BaseModel):
    """The file the worker stores in the bucket; messages only carry its key."""

    schema_version: int
    hotspot_id: str
    model_version: str
    image_width: int
    image_height: int
    cells: list[Cell]


class SegmentationDispatcher:
    """Sends pending hotspots to the worker: new ones, and those whose job got lost (see `SEGMENTATION_RESEND_AFTER`).

    A hotspot only counts as sent once its job is published, so one that could not be sent just waits for later.
    """

    def __init__(self, session_factory: sessionmaker[Session], publish: PublishJob) -> None:
        self._session_factory = session_factory
        self._publish = publish
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="segmentation-dispatcher", daemon=True)
        self._failing = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()

    def dispatch_once(self, now: datetime | None = None) -> int:
        """Sends one batch; returns how many went out, and raises if any could not be sent."""
        moment = now or datetime.now(UTC)
        with self._session_factory() as session:
            hotspots = HotspotRepository(session)
            # The claim locks the rows, so another stack skips them instead of sending them twice.
            claimed = hotspots.claim_pending_segmentations(moment - SEGMENTATION_RESEND_AFTER, DISPATCH_BATCH_SIZE)
            sent: list[uuid.UUID] = []
            try:
                for hotspot_id, image_type, mpp in claimed:
                    self._publish(
                        hotspot_id,
                        HOTSPOT_IMAGE_KEY.format(hotspot_id=hotspot_id, extension=EXTENSIONS[image_type]),
                        mpp,
                    )
                    sent.append(hotspot_id)
            finally:
                hotspots.mark_segmentations_requested(sent, moment)
        return len(sent)

    def _run(self) -> None:
        delay = DISPATCH_INTERVAL_SECONDS
        while not self._stopping.wait(delay):
            try:
                sent = self.dispatch_once()
            except Exception as exc:
                # The normal state of a stopped worker stack, so one line per outage, not one per retry.
                if not self._failing:
                    logger.warning("Segmentation jobs cannot be sent, hotspots stay pending: %s", exc)
                self._failing = True
                delay = DISPATCH_RETRY_SECONDS
                continue
            if self._failing:
                logger.info("Segmentation jobs are being sent again")
            self._failing = False
            # A full batch means there is more waiting.
            delay = 0.0 if sent == DISPATCH_BATCH_SIZE else DISPATCH_INTERVAL_SECONDS


class SegmentationResultService:
    """Saves what the worker reports. A hotspot leaves `pending` only once, so a duplicate result changes nothing, and
    a late one can never land on work an annotator has started in the meantime."""

    def __init__(self, session_factory: sessionmaker[Session], storage: ObjectStorage) -> None:
        self._session_factory = session_factory
        self._storage = storage

    def save(
        self,
        schema_version: int,
        hotspot_id: uuid.UUID,
        status: str,
        model_version: str | None = None,
        result_key: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            hotspots = HotspotRepository(session)
            # Locked until the commit, so two results for one hotspot run one after the other.
            hotspot = hotspots.get_for_update(hotspot_id)
            if hotspot is None:
                # Deleted while the model was running: its cleanup may have run before the worker's upload.
                self._storage.delete_prefix(HOTSPOT_OBJECTS_PREFIX.format(hotspot_id=hotspot_id))
                logger.info("Hotspot %s was deleted during segmentation; its result was discarded", hotspot_id)
                return
            if hotspot.segmentation_status != SegmentationStatus.PENDING:
                logger.info("Ignoring a result for hotspot %s (%s)", hotspot_id, hotspot.segmentation_status.value)
                return
            if schema_version != SCHEMA_VERSION:
                self._fail(session, hotspot, UNSUPPORTED_SCHEMA.format(schema_version=schema_version))
            elif status == STATUS_DONE and result_key is not None and model_version is not None:
                self._save_cells(session, hotspot, result_key, model_version)
            else:
                self._fail(session, hotspot, error or RESULT_UNREADABLE)

    def _save_cells(self, session: Session, hotspot: Hotspot, result_key: str, model_version: str) -> None:
        # A storage error propagates: the hotspot stays pending, and the job is sent again later.
        data = self._storage.get_bytes(result_key)
        try:
            result = SegmentationResult.model_validate_json(data)
        except ValidationError:
            logger.exception("Unreadable segmentation result %s", result_key)
            self._fail(session, hotspot, RESULT_UNREADABLE)
            return
        cells = _valid_cells(result, hotspot)
        cell_type_ids = _cell_type_ids(session, {name for _, name in cells})
        session.add_all(
            HotspotAnnotation(
                id=uuid.uuid4(),
                hotspot_id=hotspot.id,
                kind=AnnotationKind.CELL,
                source=AnnotationSource.MODEL,
                model_version=model_version,
                cell_type_id=cell_type_ids[name],
                geometry=geometry,
            )
            for geometry, name in cells
        )
        hotspot.segmentation_status, hotspot.segmentation_error = SegmentationStatus.DONE, None
        session.commit()

    def _fail(self, session: Session, hotspot: Hotspot, message: str) -> None:
        hotspot.segmentation_status, hotspot.segmentation_error = SegmentationStatus.FAILED, message
        session.commit()


def _cell_type_ids(session: Session, names: set[str]) -> dict[str, int]:
    """The id of the cell type with each name. The model's types are matched to existing ones by name (ignoring
    case) and created when missing, so a model with other classes needs no migration."""
    cell_types = CellTypeRepository(session)
    return {name: (cell_types.get_by_name(name) or cell_types.create(name)).id for name in names}


def _valid_cells(result: SegmentationResult, hotspot: Hotspot) -> list[tuple[dict[str, object], str]]:
    """Each cell as stored (its geometry inside the image, and the name of its cell type). One bad cell is dropped:
    it must not cost the hotspot its other cells."""
    cells: list[tuple[dict[str, object], str]] = []
    for cell in result.cells:
        # Seeded by a migration and protected from changes; `_cell_type_ids` creates it if it is gone regardless.
        name = (cell.cell_type or "").strip()[:CELL_TYPE_NAME_MAX_LENGTH] or UNCLASSIFIED_CELL_TYPE_NAME
        try:
            polygon = PolygonGeometry(coordinates=[cell.ring])
            geometry = fit_to_bounds(polygon, float(hotspot.image_width), float(hotspot.image_height))
        except (ValidationError, AppError):
            logger.warning("Dropped an invalid model cell on hotspot %s", hotspot.id)
            continue
        cells.append((geometry, name))
    return cells
