from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.hotspot import HotspotFolder
from app.models.organization import Organization
from app.models.slide import Slide


class OrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, organization_id: int) -> Organization | None:
        return self._session.get(Organization, organization_id)

    def get_by_name(self, name: str) -> Organization | None:
        return self._session.scalars(select(Organization).where(Organization.name == name)).first()

    def list_with_member_counts(self) -> Sequence[tuple[Organization, int]]:
        members = func.count(Account.id)
        statement = (
            select(Organization, members)
            .outerjoin(Account, Account.organization_id == Organization.id)
            .group_by(Organization.id)
            .order_by(Organization.name)
        )
        return [(organization, count) for organization, count in self._session.execute(statement).all()]

    def list_ids(self) -> Sequence[int]:
        return self._session.scalars(select(Organization.id)).all()

    def count_members(self, organization_id: int) -> int:
        return self._session.scalar(
            select(func.count(Account.id)).where(Account.organization_id == organization_id)
        ) or 0

    def is_in_use(self, organization_id: int) -> bool:
        return any(
            self._session.scalar(select(model.id).where(model.organization_id == organization_id).limit(1))
            is not None
            for model in (Account, Slide, HotspotFolder)
        )

    def save(self, organization: Organization) -> Organization:
        self._session.add(organization)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return organization

    def delete(self, organization: Organization) -> None:
        self._session.delete(organization)
        self._session.commit()
