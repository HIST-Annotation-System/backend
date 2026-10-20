import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.account import Account
from app.models.base import Base

TAG_NAME_MAX_LENGTH = 64
# Must stay >= the longest TagDimension value.
TAG_DIMENSION_MAX_LENGTH = 16


class TagDimension(enum.StrEnum):
    """An exclusive verdict: a hotspot holds at most one tag of each dimension."""

    IMPORTANCE = "importance"
    ACCURACY = "accuracy"


class Tag(Base):
    """A label experts put on hotspots, shared by everyone; a hotspot's score is the sum of its tags' weights.

    Core tags (seeded, one per dimension value) have a dimension; tags created by experts do not, and stack.
    """

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(TAG_NAME_MAX_LENGTH), unique=True)
    dimension: Mapped[TagDimension | None] = mapped_column(
        Enum(
            TagDimension,
            name="tag_dimension",
            native_enum=False,
            create_constraint=True,
            length=TAG_DIMENSION_MAX_LENGTH,
            values_callable=lambda dimensions: [item.value for item in dimensions],
        )
    )
    weight: Mapped[int]
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HotspotTag(Base):
    __tablename__ = "hotspot_tags"

    hotspot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspots.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True)
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tag: Mapped[Tag] = relationship(lazy="joined")
    added_by: Mapped[Account | None] = relationship(lazy="joined")
