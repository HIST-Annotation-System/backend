import uuid
from typing import Annotated

from fastapi import Depends

from sqlalchemy.orm import Session

from app.dependencies.auth import OrgScopeDep
from app.dependencies.resources import AppResources, ResourcesDep, SessionDep
from app.repositories.cell_types import CellTypeRepository
from app.repositories.hotspots import HotspotRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.slides import SlideRepository
from app.repositories.stats import StatsRepository
from app.repositories.tags import TagRepository
from app.services.cell_types import CellTypeService
from app.services.hotspot_annotations import HotspotAnnotationService
from app.services.hotspots import HotspotService
from app.services.organizations import OrganizationService
from app.services.slide_files import SlideFileService
from app.services.slide_ingest import SlideIngestService
from app.services.slide_upload import SlideUploadService
from app.services.slides import SlideService
from app.services.stats import StatsService
from app.services.storage_browser import StorageBrowserService
from app.services.tags import TagService


def _slide_files(resources: AppResources) -> SlideFileService:
    return SlideFileService(resources.storage, resources.settings.SLIDE_CACHE_DIR, resources.slide_download_locks)


# Request-scoped services see only the current user's organization (`OrgScopeDep`, None for the system admin);
# background work builds them unscoped.
def get_slide_service(session: SessionDep, resources: ResourcesDep, scope: OrgScopeDep) -> SlideService:
    return _slide_service(session, resources, scope)


def _slide_service(session: Session, resources: AppResources, scope: int | None) -> SlideService:
    return SlideService(
        SlideRepository(session, scope), resources.storage, _slide_files(resources), resources.slide_handles
    )


def get_slide_upload_service(session: SessionDep, resources: ResourcesDep, scope: OrgScopeDep) -> SlideUploadService:
    return SlideUploadService(SlideRepository(session, scope), resources.storage)


def get_slide_ingest_service(resources: ResourcesDep) -> SlideIngestService:
    return SlideIngestService(resources.session_factory, resources.storage, _slide_files(resources))


def get_cell_type_service(session: SessionDep) -> CellTypeService:
    return CellTypeService(CellTypeRepository(session))


def get_hotspot_service(session: SessionDep, resources: ResourcesDep, scope: OrgScopeDep) -> HotspotService:
    return _hotspot_service(session, resources, scope)


def _hotspot_service(session: Session, resources: AppResources, scope: int | None = None) -> HotspotService:
    return HotspotService(
        HotspotRepository(session, scope),
        SlideRepository(session, scope),
        _slide_service(session, resources, scope),
        resources.storage,
        TagRepository(session),
    )


def run_hotspot_batch(resources: AppResources, batch_id: uuid.UUID) -> None:
    """Background entry point: cuts out a batch with its own session (the request's is gone by then)."""
    with resources.session_factory() as session:
        _hotspot_service(session, resources).run_batch(batch_id)


def get_hotspot_annotation_service(session: SessionDep, scope: OrgScopeDep) -> HotspotAnnotationService:
    return HotspotAnnotationService(HotspotRepository(session, scope), CellTypeRepository(session))


def get_tag_service(session: SessionDep, scope: OrgScopeDep) -> TagService:
    return TagService(TagRepository(session), HotspotRepository(session, scope))


def get_organization_service(session: SessionDep) -> OrganizationService:
    return OrganizationService(OrganizationRepository(session))


def get_stats_service(session: SessionDep) -> StatsService:
    return StatsService(StatsRepository(session))


def get_storage_browser_service(resources: ResourcesDep) -> StorageBrowserService:
    return StorageBrowserService(resources.storage)


SlideServiceDep = Annotated[SlideService, Depends(get_slide_service)]
SlideUploadServiceDep = Annotated[SlideUploadService, Depends(get_slide_upload_service)]
SlideIngestServiceDep = Annotated[SlideIngestService, Depends(get_slide_ingest_service)]
CellTypeServiceDep = Annotated[CellTypeService, Depends(get_cell_type_service)]
HotspotServiceDep = Annotated[HotspotService, Depends(get_hotspot_service)]
HotspotAnnotationServiceDep = Annotated[HotspotAnnotationService, Depends(get_hotspot_annotation_service)]
TagServiceDep = Annotated[TagService, Depends(get_tag_service)]
StatsServiceDep = Annotated[StatsService, Depends(get_stats_service)]
OrganizationServiceDep = Annotated[OrganizationService, Depends(get_organization_service)]
StorageBrowserServiceDep = Annotated[StorageBrowserService, Depends(get_storage_browser_service)]
