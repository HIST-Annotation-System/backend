import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.slide import SlideStatus

REGION_MAX_SIZE = 2048


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


class SlideDetail(SlideRead):
    properties: dict[str, str]


class RegionQuery(BaseModel):
    """A level-0 rectangle to export, scaled down so its longer side is at most `max_size` pixels."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    max_size: int = Field(default=REGION_MAX_SIZE, gt=0)
