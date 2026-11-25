import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.organization import Organization


class Role(str, enum.Enum):
    # System administrator: no organization, sees and manages every organization.
    ADMIN = "ADMIN"
    ANNOTATOR = "ANNOTATOR"
    ORG_ADMIN = "ORG_ADMIN"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False, default=Role.ANNOTATOR)
    is_first_login: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Null only for system administrators.
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )

    # Relationships
    organization: Mapped["Organization | None"] = relationship(lazy="joined")
    profile: Mapped["AnnotatorProfile"] = relationship(
        "AnnotatorProfile", back_populates="account", uselist=False, cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="account", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    token: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    account: Mapped["Account"] = relationship("Account", back_populates="refresh_tokens")
