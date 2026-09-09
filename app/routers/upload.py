from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.dependencies.storage import get_s3_client
from app.services.storage import StorageUploadService
from app.schemas.upload import (
    StartUploadRequest,
    StartUploadResponse,
    PresignedUrlsRequest,
    PresignedUrlsResponse,
    RefreshUrlsRequest,
    RefreshUrlsResponse,
    CompleteUploadRequest,
    CompleteUploadResponse
)
from app.exceptions.storage import StorageException
import uuid

router = APIRouter()

def get_storage_upload_service(s3_client=Depends(get_s3_client)) -> StorageUploadService:
    return StorageUploadService(s3_client)

def mock_process_wsi(key: str):
    """Simulates background WSI processing (e.g. converting to patches)"""
    print(f"[BACKGROUND TASK] Started processing WSI image: {key}")
    import time
    time.sleep(2)
    print(f"[BACKGROUND TASK] Finished processing WSI image: {key}")

@router.post("/start", response_model=StartUploadResponse)
def start_upload(req: StartUploadRequest, service: StorageUploadService = Depends(get_storage_upload_service)):
    # Prepend UUID to the original filename to prevent collisions
    unique_key = f"wsi/{uuid.uuid4()}-{req.filename}"
    
    try:
        upload_id = service.create_multipart_upload(unique_key)
        return StartUploadResponse(upload_id=upload_id, key=unique_key)
    except StorageException as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/presigned-urls", response_model=PresignedUrlsResponse)
def generate_presigned_urls(req: PresignedUrlsRequest, service: StorageUploadService = Depends(get_storage_upload_service)):
    try:
        urls = service.get_presigned_urls_for_multipart(req.key, req.upload_id, req.total_parts)
        return PresignedUrlsResponse(urls=urls)
    except StorageException as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/refresh-urls", response_model=RefreshUrlsResponse)
def refresh_urls(req: RefreshUrlsRequest, service: StorageUploadService = Depends(get_storage_upload_service)):
    try:
        urls = service.refresh_presigned_urls_for_parts(req.key, req.upload_id, req.pending_parts)
        return RefreshUrlsResponse(urls=urls)
    except StorageException as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/complete", response_model=CompleteUploadResponse)
def complete_upload(
    req: CompleteUploadRequest, 
    background_tasks: BackgroundTasks,
    service: StorageUploadService = Depends(get_storage_upload_service)
):
    try:
        parts_dict = [{"PartNumber": p.PartNumber, "ETag": p.ETag} for p in req.parts]
        service.complete_multipart_upload(req.key, req.upload_id, parts_dict)
        
        background_tasks.add_task(mock_process_wsi, req.key)
        
        return CompleteUploadResponse(success=True, message="Upload completed successfully. Processing started.")
    except StorageException as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
