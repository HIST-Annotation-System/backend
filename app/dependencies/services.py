import uuid
from typing import Annotated

from fastapi import Depends

from sqlalchemy.orm import Session

from app.dependencies.resources import AppResources, ResourcesDep, SessionDep
from app.repositories.cell_types import CellTypeRepository
from app.repositories.hotspots import HotspotRepository
from app.repositories.slides import SlideRepository
from app.services.cell_types import CellTypeService
from app.services.hotspot_annotations import HotspotAnnotationService
from app.services.hotspots import HotspotService
from app.services.slide_files import SlideFileService
from app.services.slide_ingest import SlideIngestService
from app.services.slide_upload import SlideUploadService
from app.services.slides import SlideService
from app.services.storage_browser import StorageBrowserService


def _slide_files(resources: AppResources) -> SlideFileService:
    return SlideFileService(resources.storage, resources.settings.SLIDE_CACHE_DIR, resources.slide_download_locks)


def get_slide_service(session: SessionDep, resources: ResourcesDep) -> SlideService:
    return SlideService(SlideRepository(session), resources.storage, _slide_files(resources), resources.slide_handles)


def get_slide_upload_service(session: SessionDep, resources: ResourcesDep) -> SlideUploadService:
    return SlideUploadService(SlideRepository(session), resources.storage)


def get_slide_ingest_service(resources: ResourcesDep) -> SlideIngestService:
    return SlideIngestService(resources.session_factory, resources.storage, _slide_files(resources))


def get_cell_type_service(session: SessionDep) -> CellTypeService:
    return CellTypeService(CellTypeRepository(session))


def get_hotspot_service(session: SessionDep, resources: ResourcesDep) -> HotspotService:
    return _hotspot_service(session, resources)


def _hotspot_service(session: Session, resources: AppResources) -> HotspotService:
    return HotspotService(
        HotspotRepository(session),
        SlideRepository(session),
        get_slide_service(session, resources),
        resources.storage,
    )


def run_hotspot_batch(resources: AppResources, batch_id: uuid.UUID) -> None:
    """Background entry point: cuts out a batch with its own session (the request's is gone by then)."""
    with resources.session_factory() as session:
        _hotspot_service(session, resources).run_batch(batch_id)


def get_hotspot_annotation_service(session: SessionDep) -> HotspotAnnotationService:
    return HotspotAnnotationService(HotspotRepository(session), CellTypeRepository(session))


def get_storage_browser_service(resources: ResourcesDep) -> StorageBrowserService:
    return StorageBrowserService(resources.storage)


SlideServiceDep = Annotated[SlideService, Depends(get_slide_service)]
SlideUploadServiceDep = Annotated[SlideUploadService, Depends(get_slide_upload_service)]
SlideIngestServiceDep = Annotated[SlideIngestService, Depends(get_slide_ingest_service)]
CellTypeServiceDep = Annotated[CellTypeService, Depends(get_cell_type_service)]
HotspotServiceDep = Annotated[HotspotService, Depends(get_hotspot_service)]
HotspotAnnotationServiceDep = Annotated[HotspotAnnotationService, Depends(get_hotspot_annotation_service)]
StorageBrowserServiceDep = Annotated[StorageBrowserService, Depends(get_storage_browser_service)]
