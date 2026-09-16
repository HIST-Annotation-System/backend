from typing import Annotated

from fastapi import APIRouter, Query

from app.dependencies.services import StorageBrowserServiceDep
from app.schemas.storage import DownloadUrlResponse, StorageBrowseResponse

router = APIRouter()


@router.get("/browse", response_model=StorageBrowseResponse)
def browse_storage(
    service: StorageBrowserServiceDep,
    prefix: Annotated[str, Query()] = "",
    continuation_token: Annotated[str | None, Query()] = None,
) -> StorageBrowseResponse:
    return service.browse(prefix, continuation_token)


@router.get("/download-url", response_model=DownloadUrlResponse)
def get_download_url(key: Annotated[str, Query()], service: StorageBrowserServiceDep) -> DownloadUrlResponse:
    return DownloadUrlResponse(url=service.get_download_url(key))
