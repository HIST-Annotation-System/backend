import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.celery_app import build_celery_app
from app.core.config import Settings
from app.services.segmentation import (
    JOBS_QUEUE,
    SCHEMA_VERSION,
    SEGMENTATION_RESEND_AFTER,
    TASK_RUN,
    SegmentationDispatcher,
)
from app.services.slide_files import SlideDownloadLocks
from app.services.slides import SlideHandleCache
from app.services.storage import ObjectStorage, ObjectStorageService

# Ingestion is mostly a download plus a short analysis, so two slides uploaded one after another
# don't have to wait for each other; more would compete with tile serving for CPU and disk.
INGEST_POOL_WORKERS = 2
INGEST_POOL_THREAD_NAME_PREFIX = "ingest"
# Connections kept open between requests. Over the SSM tunnel a new one takes seconds, and connections beyond the
# pool are closed after use, so a viewer loading several things at once must fit in it.
DB_POOL_SIZE = 10
DB_MAX_OVERFLOW = 5
# Hotspot batches are cut out one at a time, so they never compete with ingestion or with each other.
HOTSPOT_POOL_WORKERS = 1
HOTSPOT_POOL_THREAD_NAME_PREFIX = "hotspots"


@dataclass(frozen=True)
class AppResources:
    """Process-wide infrastructure, created once at startup and injected where needed (never a global)."""

    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    storage: ObjectStorage
    slide_handles: SlideHandleCache
    slide_download_locks: SlideDownloadLocks
    # Slide ingestion runs here, not on the request threadpool that also serves Deep Zoom tiles.
    ingest_pool: ThreadPoolExecutor
    hotspot_pool: ThreadPoolExecutor
    # None unless SEGMENTATION_ENABLED: hotspots then wait as `pending` for a stack that has a worker.
    segmentation_dispatcher: SegmentationDispatcher | None = None

    def close(self) -> None:
        if self.segmentation_dispatcher is not None:
            self.segmentation_dispatcher.stop()
        self.ingest_pool.shutdown(wait=False, cancel_futures=True)
        self.hotspot_pool.shutdown(wait=False, cancel_futures=True)
        self.engine.dispose()


def _build_segmentation_dispatcher(settings: Settings, session_factory: sessionmaker[Session]) -> SegmentationDispatcher:
    celery_app = build_celery_app(settings)

    def publish(hotspot_id: uuid.UUID, image_key: str, mpp: float | None) -> None:
        celery_app.send_task(
            TASK_RUN,
            kwargs={
                "schema_version": SCHEMA_VERSION,
                "hotspot_id": str(hotspot_id),
                "image_key": image_key,
                "mpp": mpp,
            },
            queue=JOBS_QUEUE,
            # A failure is handled by trying again later, and must not block the dispatcher's thread on retries.
            retry=False,
            expires=SEGMENTATION_RESEND_AFTER.total_seconds(),
        )

    return SegmentationDispatcher(session_factory, publish)


def build_resources(settings: Settings) -> AppResources:
    storage = ObjectStorageService(settings)
    storage.check_bucket()
    settings.SLIDE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        str(settings.DATABASE_URL), pool_pre_ping=True, pool_size=DB_POOL_SIZE, max_overflow=DB_MAX_OVERFLOW
    )
    # Committed entities stay readable while responses are built.
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    dispatcher = _build_segmentation_dispatcher(settings, session_factory) if settings.SEGMENTATION_ENABLED else None
    if dispatcher is not None:
        dispatcher.start()
    return AppResources(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        storage=storage,
        slide_handles=SlideHandleCache(),
        slide_download_locks=SlideDownloadLocks(),
        ingest_pool=ThreadPoolExecutor(max_workers=INGEST_POOL_WORKERS, thread_name_prefix=INGEST_POOL_THREAD_NAME_PREFIX),
        hotspot_pool=ThreadPoolExecutor(
            max_workers=HOTSPOT_POOL_WORKERS, thread_name_prefix=HOTSPOT_POOL_THREAD_NAME_PREFIX
        ),
        segmentation_dispatcher=dispatcher,
    )


def get_resources(request: Request) -> AppResources:
    return cast(AppResources, request.app.state.resources)


ResourcesDep = Annotated[AppResources, Depends(get_resources)]


def get_session(resources: ResourcesDep) -> Iterator[Session]:
    with resources.session_factory() as session:
        yield session
        session.commit()


SessionDep = Annotated[Session, Depends(get_session)]
