from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies.auth import require_admin
from app.dependencies.services import StorageBrowserServiceDep
from app.schemas.storage import DownloadUrlResponse, StorageBrowseResponse

# The whole bucket is visible here (including other teams' files), so it is an administrator tool.
router = APIRouter(dependencies=[Depends(require_admin)])


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
