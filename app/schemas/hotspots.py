import enum
import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.constants import NAME_MAX_LENGTH, NAME_MIN_LENGTH
from app.models.hotspot import HotspotFeature, HotspotImageType

HOTSPOT_UPDATE_EMPTY = "Provide name and/or folder_id."

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=NAME_MIN_LENGTH, max_length=NAME_MAX_LENGTH)]


class HotspotSort(enum.StrEnum):
    """What the catalog is ordered by: creation time, or one of the HotspotFeature descriptors."""

    CREATED_AT = "created_at"
    ENTROPY = HotspotFeature.ENTROPY.value
    GLCM_CONTRAST = HotspotFeature.GLCM_CONTRAST.value
    EDGE_DENSITY = HotspotFeature.EDGE_DENSITY.value
    COLORFULNESS = HotspotFeature.COLORFULNESS.value


class SortOrder(enum.StrEnum):
    ASC = "asc"
    DESC = "desc"


class HotspotFolderWrite(BaseModel):
    name: Name


class HotspotFolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    hotspot_count: int
    created_at: datetime


class HotspotCreate(BaseModel):
    """A square of a slide to capture, in level-0 slide pixels."""

    slide_id: uuid.UUID
    folder_id: uuid.UUID
    name: Name
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    size: int = Field(gt=0)


class HotspotUpdate(BaseModel):
    name: Name | None = None
    folder_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        if self.name is None and self.folder_id is None:
            raise ValueError(HOTSPOT_UPDATE_EMPTY)
        return self


class HotspotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    folder_id: uuid.UUID
    name: str
    # All null for an uploaded image; slide_id alone becomes null when the source slide is deleted.
    slide_id: uuid.UUID | None
    slide_name: str | None
    x: int | None
    y: int | None
    size: int | None
    image_width: int
    image_height: int
    image_type: HotspotImageType
    mpp: float | None
    # HotspotFeature value -> descriptor; null when it could not be computed (e.g. missing thumbnail).
    features: dict[str, float] | None
    created_at: datetime
