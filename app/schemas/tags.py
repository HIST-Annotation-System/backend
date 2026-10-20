import uuid
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.tag import TAG_NAME_MAX_LENGTH, TagDimension

TAG_UPDATE_EMPTY = "Provide name and/or weight."
TAGS_BULK_EMPTY = "Provide tags to add and/or remove."
TAG_WEIGHT_MAX = 1000
CUSTOM_TAG_DEFAULT_WEIGHT = 5
# Covers "select all" across every folder of a realistic catalog.
HOTSPOT_TAGS_BULK_MAX = 2000

TagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TAG_NAME_MAX_LENGTH)]
Weight = Annotated[int, Field(ge=0, le=TAG_WEIGHT_MAX)]


class TagCreate(BaseModel):
    name: TagName
    weight: Weight = CUSTOM_TAG_DEFAULT_WEIGHT


class TagUpdate(BaseModel):
    name: TagName | None = None
    weight: Weight | None = None

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        if self.name is None and self.weight is None:
            raise ValueError(TAG_UPDATE_EMPTY)
        return self


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    # Null for tags created by experts.
    dimension: TagDimension | None
    weight: int


class HotspotTagsBulk(BaseModel):
    """Tags to remove from, then add to, every listed hotspot."""

    hotspot_ids: list[uuid.UUID] = Field(min_length=1, max_length=HOTSPOT_TAGS_BULK_MAX)
    add: list[int] = []
    remove: list[int] = []

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        if not self.add and not self.remove:
            raise ValueError(TAGS_BULK_EMPTY)
        return self
