import hashlib
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.constants import NAME_MAX_LENGTH, NAME_MIN_LENGTH
from app.models.slide import SlideStatus

REGION_MAX_SIZE = 2048
SLIDE_VERSION_LENGTH = 16


def slide_version(name: str) -> str:
    """Optimistic-concurrency token of a slide, sent back by clients in `If-Match` on rename/delete.

    Derived from the only user-editable field instead of a stored counter, so it needs no schema
    change; status/progress are left out so a background ingest never turns an edit into a conflict.
    """
    return hashlib.sha256(name.encode()).hexdigest()[:SLIDE_VERSION_LENGTH]


class Offset(BaseModel):
    x: int
    y: int


class Rect(Offset):
    width: int
    height: int


class SlideRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    original_filename: str
    status: SlideStatus
    error_message: str | None
    ingest_progress: int | None
    vendor: str | None
    width: int | None
    height: int | None
    level_count: int | None
    mpp_x: float | None
    objective_power: float | None
    file_size_bytes: int | None
    image_offset: Offset
    tissue_bbox: Rect | None
    created_at: datetime
    # Username of the admin who uploaded/imported it - shown while the upload is still running, too.
    uploaded_by: str | None = Field(validation_alias="uploaded_by_name")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def version(self) -> str:
        return slide_version(self.name)


class SlideDetail(SlideRead):
    properties: dict[str, str]


class SlideRenameRequest(BaseModel):
    name: str = Field(min_length=NAME_MIN_LENGTH, max_length=NAME_MAX_LENGTH)


class RegionQuery(BaseModel):
    """A level-0 rectangle to export, scaled down so its longer side is at most `max_size` pixels."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    max_size: int = Field(default=REGION_MAX_SIZE, gt=0)
