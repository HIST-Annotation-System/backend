from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AnnotationGroup(Base):
    __tablename__ = "annotation_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    profiles: Mapped[list["AnnotatorProfile"]] = relationship("AnnotatorProfile", back_populates="group")
