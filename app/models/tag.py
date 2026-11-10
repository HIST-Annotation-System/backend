import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
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
    """A label experts put on hotspots; a hotspot's score is the sum of its tags' weights.

    Core tags (seeded, one per dimension value) have a dimension: they are what an annotator rates a hotspot with
    (`HotspotRating`, one per annotator). Tags created by experts have none; they are shared and stack.
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
    """A custom tag (without a dimension) on a hotspot, shared by everyone."""

    __tablename__ = "hotspot_tags"

    hotspot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspots.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True)
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tag: Mapped[Tag] = relationship(lazy="joined")
    added_by: Mapped[Account | None] = relationship(lazy="joined")


class HotspotRating(Base):
    """One annotator's importance and accuracy verdict on a hotspot (either may be unset); its raters are its group."""

    __tablename__ = "hotspot_ratings"
    __table_args__ = (UniqueConstraint("hotspot_id", "account_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hotspot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspots.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    importance_tag_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id", ondelete="RESTRICT"))
    accuracy_tag_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account: Mapped[Account] = relationship(lazy="joined")
    importance_tag: Mapped[Tag | None] = relationship(foreign_keys=[importance_tag_id], lazy="joined")
    accuracy_tag: Mapped[Tag | None] = relationship(foreign_keys=[accuracy_tag_id], lazy="joined")

    def tag(self, dimension: TagDimension) -> Tag | None:
        return self.importance_tag if dimension == TagDimension.IMPORTANCE else self.accuracy_tag

    def set_tag(self, dimension: TagDimension, tag: Tag | None) -> None:
        if dimension == TagDimension.IMPORTANCE:
            self.importance_tag = tag
        else:
            self.accuracy_tag = tag

    @property
    def is_empty(self) -> bool:
        return self.importance_tag is None and self.accuracy_tag is None
