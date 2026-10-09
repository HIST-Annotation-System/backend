from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.services.slide_files import SlideDownloadLocks
from app.services.slides import SlideHandleCache
from app.services.storage import ObjectStorage, ObjectStorageService

# Ingestion is mostly a download plus a short analysis, so two slides uploaded one after another
# don't have to wait for each other; more would compete with tile serving for CPU and disk.
INGEST_POOL_WORKERS = 2
INGEST_POOL_THREAD_NAME_PREFIX = "ingest"


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

    def close(self) -> None:
        self.ingest_pool.shutdown(wait=False, cancel_futures=True)
        self.engine.dispose()


def build_resources(settings: Settings) -> AppResources:
    storage = ObjectStorageService(settings)
    storage.check_bucket()
    settings.SLIDE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(str(settings.DATABASE_URL), pool_pre_ping=True)
    return AppResources(
        settings=settings,
        engine=engine,
        # Committed entities stay readable while responses are built.
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        storage=storage,
        slide_handles=SlideHandleCache(),
        slide_download_locks=SlideDownloadLocks(),
        ingest_pool=ThreadPoolExecutor(max_workers=INGEST_POOL_WORKERS, thread_name_prefix=INGEST_POOL_THREAD_NAME_PREFIX),
    )


def get_resources(request: Request) -> AppResources:
    return cast(AppResources, request.app.state.resources)


ResourcesDep = Annotated[AppResources, Depends(get_resources)]


def get_session(resources: ResourcesDep) -> Iterator[Session]:
    with resources.session_factory() as session:
        yield session
        session.commit()


SessionDep = Annotated[Session, Depends(get_session)]
