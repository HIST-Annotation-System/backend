import enum
import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.constants import NAME_MAX_LENGTH, NAME_MIN_LENGTH
from app.models.hotspot import HotspotBatchStatus, HotspotFeature, HotspotImageType
from app.schemas.tags import TagRead

HOTSPOT_UPDATE_EMPTY = "Provide name, folder_id and/or reannotated."
DUPLICATE_BOXES = "Each box may be listed only once."
HOTSPOT_BATCH_MAX_BOXES = 100

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=NAME_MIN_LENGTH, max_length=NAME_MAX_LENGTH)]


class HotspotSort(enum.StrEnum):
    """What the catalog is ordered by: creation time, one of the HotspotFeature descriptors, the summed tag
    weights, or the weight of the importance/accuracy tag."""

    CREATED_AT = "created_at"
    ENTROPY = HotspotFeature.ENTROPY.value
    GLCM_CONTRAST = HotspotFeature.GLCM_CONTRAST.value
    EDGE_DENSITY = HotspotFeature.EDGE_DENSITY.value
    COLORFULNESS = HotspotFeature.COLORFULNESS.value
    SCORE = "score"
    IMPORTANCE = "importance"
    ACCURACY = "accuracy"


class HotspotStatus(enum.StrEnum):
    """Where a hotspot is in the review: tagged means both importance and accuracy are set."""

    UNTAGGED = "untagged"
    TO_REANNOTATE = "to_reannotate"
    REANNOTATED = "reannotated"


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


BoxCorner = tuple[Annotated[int, Field(ge=0)], Annotated[int, Field(ge=0)]]


class HotspotBatchCreate(BaseModel):
    """Fixed-size slide squares to cut out into hotspots, each named `{name_prefix} ({x}, {y})`."""

    # Chosen by the client, so a retried request does not start the batch twice.
    id: uuid.UUID
    slide_id: uuid.UUID
    folder_id: uuid.UUID
    name_prefix: Name
    # Level-0 [x, y] top-left corner of each box to cut out (laid out on a grid or painted freely).
    boxes: list[BoxCorner] = Field(min_length=1, max_length=HOTSPOT_BATCH_MAX_BOXES)

    @model_validator(mode="after")
    def _require_unique_boxes(self) -> Self:
        if len(set(self.boxes)) != len(self.boxes):
            raise ValueError(DUPLICATE_BOXES)
        return self


class HotspotBatchRead(BaseModel):
    id: uuid.UUID
    slide_id: uuid.UUID
    slide_name: str
    folder_id: uuid.UUID
    folder_name: str
    total: int
    done: int
    status: HotspotBatchStatus
    error_message: str | None
    created_at: datetime


class HotspotUpdate(BaseModel):
    name: Name | None = None
    folder_id: uuid.UUID | None = None
    reannotated: bool | None = None

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        if self.name is None and self.folder_id is None and self.reannotated is None:
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
    tags: list[TagRead]
    score: int
    reannotated_at: datetime | None
    # Who set the latest importance/accuracy verdict.
    assessed_by: str | None
    annotation_count: int
    created_at: datetime
