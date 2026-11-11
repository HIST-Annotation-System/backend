import enum
import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.constants import NAME_MAX_LENGTH, NAME_MIN_LENGTH
from app.models.hotspot import HotspotBatchStatus, HotspotFeature, HotspotImageType, SegmentationStatus
from app.schemas.reannotation import ReannotationSummary
from app.schemas.tags import TagRead

HOTSPOT_UPDATE_EMPTY = "Provide name, folder_id and/or reannotated."
DUPLICATE_BOXES = "Each box may be listed only once."
HOTSPOT_BATCH_MAX_BOXES = 100

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=NAME_MIN_LENGTH, max_length=NAME_MAX_LENGTH)]


class HotspotSort(enum.StrEnum):
    """What the catalog is ordered by: creation time, one of the HotspotFeature descriptors, the score, the
    average importance/accuracy of the ratings, or how many annotators rated the hotspot."""

    CREATED_AT = "created_at"
    ENTROPY = HotspotFeature.ENTROPY.value
    GLCM_CONTRAST = HotspotFeature.GLCM_CONTRAST.value
    EDGE_DENSITY = HotspotFeature.EDGE_DENSITY.value
    COLORFULNESS = HotspotFeature.COLORFULNESS.value
    SCORE = "score"
    IMPORTANCE = "importance"
    ACCURACY = "accuracy"
    RATING_COUNT = "rating_count"


class HotspotStatus(enum.StrEnum):
    """Where a hotspot is in the review: tagged means its ratings set both importance and accuracy."""

    UNTAGGED = "untagged"
    TO_REANNOTATE = "to_reannotate"
    REANNOTATED = "reannotated"


class SortOrder(enum.StrEnum):
    ASC = "asc"
    DESC = "desc"


class HotspotFolderWrite(BaseModel):
    name: Name


class HotspotFolderCreate(HotspotFolderWrite):
    # Only the system admin chooses; everyone else creates folders in their own organization.
    organization_id: int | None = None


class HotspotFolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    organization_id: int
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


class BoxFeaturesRequest(BaseModel):
    """Slide squares whose image descriptors to compute before they are cut out (repeated corners are fine)."""

    slide_id: uuid.UUID
    boxes: list[BoxCorner] = Field(min_length=1, max_length=HOTSPOT_BATCH_MAX_BOXES)


class BoxFeatures(BaseModel):
    x: int
    y: int
    # The same descriptors a hotspot cut from this square gets (see app.services.hotspot_features).
    features: dict[str, float]


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


class RatingRead(BaseModel):
    """One annotator's rating of a hotspot (either tag may be unset)."""

    account_id: int
    username: str
    full_name: str | None
    importance: TagRead | None
    accuracy: TagRead | None
    updated_at: datetime


class HotspotRead(BaseModel):
    """A hotspot as the requesting user sees it: the group's verdict (core tags, score, `assessed_by`, `ratings`) is
    hidden from annotators who have not rated it yet, so other people's ratings can't sway theirs."""

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
    # The team's verdict (the core tag nearest to the average rating of each dimension, see
    # app.services.hotspot_ratings) first, then the custom tags by name.
    tags: list[TagRead] = []
    # Custom tag weights plus the rounded average weights of the ratings.
    score: int = 0
    reannotated_at: datetime | None
    # Who changed a rating last.
    assessed_by: str | None = None
    status: HotspotStatus = HotspotStatus.UNTAGGED
    rating_count: int = 0
    # Built per viewer by HotspotReader.
    reannotation: ReannotationSummary = ReannotationSummary()
    my_rating: RatingRead | None = None
    # Built per viewer by HotspotReader, not read from the model's `ratings`.
    ratings: list[RatingRead] | None = Field(default=None, validation_alias="visible_ratings")
    # Cells annotated by people; untouched model cells don't count.
    annotation_count: int
    segmentation_status: SegmentationStatus
    segmentation_error: str | None
    created_at: datetime
