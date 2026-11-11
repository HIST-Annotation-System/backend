from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AnnotationGroup(Base):
    """Old annotator groups, no longer used by the app. Kept mapped only because the table and
    `annotator_profiles.group_id` still exist."""

    __tablename__ = "annotation_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    profiles: Mapped[list["AnnotatorProfile"]] = relationship("AnnotatorProfile", back_populates="group")
