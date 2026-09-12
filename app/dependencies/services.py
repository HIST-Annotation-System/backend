from typing import Annotated

from fastapi import Depends

from app.dependencies.resources import AppResources, ResourcesDep, SessionDep
from app.repositories.annotations import AnnotationRepository
from app.repositories.slides import SlideRepository
from app.services.annotations import AnnotationService
from app.services.slide_files import SlideFileService
from app.services.slide_ingest import SlideIngestService
from app.services.slide_upload import SlideUploadService
from app.services.slides import SlideService


def _slide_files(resources: AppResources) -> SlideFileService:
    return SlideFileService(resources.storage, resources.settings.SLIDE_CACHE_DIR)


def get_slide_service(session: SessionDep, resources: ResourcesDep) -> SlideService:
    return SlideService(SlideRepository(session), resources.storage, _slide_files(resources), resources.slide_handles)


def get_slide_upload_service(session: SessionDep, resources: ResourcesDep) -> SlideUploadService:
    return SlideUploadService(SlideRepository(session), resources.storage)


def get_slide_ingest_service(resources: ResourcesDep) -> SlideIngestService:
    return SlideIngestService(resources.session_factory, resources.storage, _slide_files(resources))


def get_annotation_service(session: SessionDep) -> AnnotationService:
    return AnnotationService(AnnotationRepository(session), SlideRepository(session))


SlideServiceDep = Annotated[SlideService, Depends(get_slide_service)]
SlideUploadServiceDep = Annotated[SlideUploadService, Depends(get_slide_upload_service)]
SlideIngestServiceDep = Annotated[SlideIngestService, Depends(get_slide_ingest_service)]
AnnotationServiceDep = Annotated[AnnotationService, Depends(get_annotation_service)]
