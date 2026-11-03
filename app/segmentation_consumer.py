"""The Celery worker that saves segmentation results; runs apart from the API (see docker-compose.yml).

    celery -A app.segmentation_consumer worker -Q segmentation-results --pool=solo --concurrency=1
"""

import uuid
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.celery_app import build_celery_app
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.segmentation import TASK_SAVE_RESULT, SegmentationResultService
from app.services.storage import ObjectStorageService

configure_logging(settings.LOG_LEVEL)

celery_app = build_celery_app(settings)


@lru_cache(maxsize=1)
def _service() -> SegmentationResultService:
    engine = create_engine(str(settings.DATABASE_URL), pool_pre_ping=True)
    # Committed entities stay readable while the result is logged.
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SegmentationResultService(factory, ObjectStorageService(settings))


@celery_app.task(name=TASK_SAVE_RESULT)  # type: ignore[untyped-decorator]
def save_segmentation(
    schema_version: int,
    hotspot_id: str,
    status: str,
    model_version: str | None = None,
    result_key: str | None = None,
    error: str | None = None,
) -> None:
    # On an exception the message is gone, but the hotspot stays pending, so the job is sent again later.
    _service().save(schema_version, uuid.UUID(hotspot_id), status, model_version, result_key, error)
