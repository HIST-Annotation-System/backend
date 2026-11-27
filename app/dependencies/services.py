import uuid
from typing import Annotated

from fastapi import Depends, Query

from sqlalchemy.orm import Session

from app.dependencies.auth import OrgScopeDep
from app.dependencies.resources import AppResources, ResourcesDep, SessionDep
from app.exceptions.errors import NotFoundError
from app.repositories.cell_types import CellTypeRepository
from app.repositories.hotspots import HotspotRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.priority_weights import PriorityWeightRepository
from app.repositories.slides import SlideRepository
from app.repositories.stats import StatsRepository
from app.repositories.tags import TagRepository
from app.services.cell_types import CellTypeService
from app.services.hotspot_annotations import HotspotAnnotationService
from app.services.hotspot_reannotation import HotspotReannotationService
from app.services.hotspots import HotspotService
from app.services.organizations import ORGANIZATION_NOT_FOUND, OrganizationService
from app.services.priority import PriorityWeightService
from app.services.slide_files import SlideFileService
from app.services.slide_ingest import SlideIngestService
from app.services.slide_upload import SlideUploadService
from app.services.slides import SlideService
from app.services.stats import StatsService
from app.services.storage_browser import StorageBrowserService
from app.services.tags import TagService


def _slide_files(resources: AppResources) -> SlideFileService:
    return SlideFileService(
        resources.storage,
        resources.settings.SLIDE_CACHE_DIR,
        resources.slide_download_locks,
        resources.slide_handles.evict,
    )


# Request-scoped services see only the current user's organization (`OrgScopeDep`, None for the system admin);
# background work builds them unscoped.
def get_slide_service(session: SessionDep, resources: ResourcesDep, scope: OrgScopeDep) -> SlideService:
    return _slide_service(session, resources, scope, in_request=True)


def _slide_service(session: Session, resources: AppResources, scope: int | None, in_request: bool) -> SlideService:
    # Requests never wait for a slide download: it runs on the ingest pool while they answer 503.
    download_pool = resources.ingest_pool if in_request else None
    return SlideService(
        SlideRepository(session, scope),
        resources.storage,
        _slide_files(resources),
        resources.slide_handles,
        download_pool,
    )


def get_slide_upload_service(session: SessionDep, resources: ResourcesDep, scope: OrgScopeDep) -> SlideUploadService:
    return SlideUploadService(SlideRepository(session, scope), resources.storage)


def get_slide_ingest_service(resources: ResourcesDep) -> SlideIngestService:
    return SlideIngestService(resources.session_factory, resources.storage, _slide_files(resources))


def get_cell_type_service(session: SessionDep) -> CellTypeService:
    return CellTypeService(CellTypeRepository(session))


def get_hotspot_service(session: SessionDep, resources: ResourcesDep, scope: OrgScopeDep) -> HotspotService:
    return _hotspot_service(session, resources, scope, in_request=True)


def _hotspot_service(
    session: Session, resources: AppResources, scope: int | None = None, in_request: bool = False
) -> HotspotService:
    return HotspotService(
        HotspotRepository(session, scope),
        SlideRepository(session, scope),
        _slide_service(session, resources, scope, in_request),
        resources.storage,
        PriorityWeightRepository(session),
    )


def run_hotspot_batch(resources: AppResources, batch_id: uuid.UUID) -> None:
    """Background entry point: cuts out a batch with its own session (the request's is gone by then)."""
    with resources.session_factory() as session:
        _hotspot_service(session, resources).run_batch(batch_id)


def get_hotspot_annotation_service(session: SessionDep, scope: OrgScopeDep) -> HotspotAnnotationService:
    return HotspotAnnotationService(HotspotRepository(session, scope), CellTypeRepository(session))


def get_hotspot_reannotation_service(session: SessionDep, scope: OrgScopeDep) -> HotspotReannotationService:
    return HotspotReannotationService(HotspotRepository(session, scope), OrganizationRepository(session))


def get_tag_service(session: SessionDep, scope: OrgScopeDep) -> TagService:
    return TagService(TagRepository(session, scope), HotspotRepository(session, scope))


def get_priority_weight_service(session: SessionDep) -> PriorityWeightService:
    return PriorityWeightService(PriorityWeightRepository(session))


def get_organization_service(session: SessionDep) -> OrganizationService:
    return OrganizationService(OrganizationRepository(session))


def get_stats_service(
    session: SessionDep, scope: OrgScopeDep, organization_id: Annotated[int | None, Query()] = None
) -> StatsService:
    """An organization's admin sees their organization; the system admin every one, or the `organization_id` one."""
    if scope is None and organization_id is not None:
        scope = OrganizationService(OrganizationRepository(session)).require(organization_id).id
    elif scope is not None and organization_id not in (None, scope):
        raise NotFoundError(ORGANIZATION_NOT_FOUND.format(organization_id=organization_id))
    return StatsService(StatsRepository(session, scope))


def get_storage_browser_service(resources: ResourcesDep) -> StorageBrowserService:
    return StorageBrowserService(resources.storage)


SlideServiceDep = Annotated[SlideService, Depends(get_slide_service)]
SlideUploadServiceDep = Annotated[SlideUploadService, Depends(get_slide_upload_service)]
SlideIngestServiceDep = Annotated[SlideIngestService, Depends(get_slide_ingest_service)]
CellTypeServiceDep = Annotated[CellTypeService, Depends(get_cell_type_service)]
HotspotServiceDep = Annotated[HotspotService, Depends(get_hotspot_service)]
HotspotAnnotationServiceDep = Annotated[HotspotAnnotationService, Depends(get_hotspot_annotation_service)]
HotspotReannotationServiceDep = Annotated[
    HotspotReannotationService, Depends(get_hotspot_reannotation_service)
]
TagServiceDep = Annotated[TagService, Depends(get_tag_service)]
PriorityWeightServiceDep = Annotated[PriorityWeightService, Depends(get_priority_weight_service)]
StatsServiceDep = Annotated[StatsService, Depends(get_stats_service)]
OrganizationServiceDep = Annotated[OrganizationService, Depends(get_organization_service)]
StorageBrowserServiceDep = Annotated[StorageBrowserService, Depends(get_storage_browser_service)]
