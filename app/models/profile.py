from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

annotator_category_table = Table(
    "annotator_category",
    Base.metadata,
    Column("annotator_id", Integer, ForeignKey("annotator_profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", Integer, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class AnnotatorProfile(Base):
    __tablename__ = "annotator_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    can_upload: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("annotation_groups.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    account: Mapped["Account"] = relationship("Account", back_populates="profile")
    group: Mapped["AnnotationGroup"] = relationship("AnnotationGroup", back_populates="profiles")
    categories: Mapped[list["Category"]] = relationship(
        "Category", secondary=annotator_category_table, cascade="all, delete"
    )
