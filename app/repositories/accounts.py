from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Result, delete, select, update
from sqlalchemy.orm import Session, joinedload

from app.models.account import Account, RefreshToken
from app.models.profile import AnnotatorProfile


class AccountRepository:
    def __init__(self, session: Session):
        self._session = session

    def get_by_username(self, username: str) -> Account | None:
        return self._session.execute(
            select(Account)
            .options(
                joinedload(Account.profile).joinedload(AnnotatorProfile.categories),
            )
            .where(Account.username == username)
        ).unique().scalar_one_or_none()

    def get_by_id(self, account_id: int) -> Account | None:
        return self._session.execute(
            select(Account)
            .options(
                joinedload(Account.profile).joinedload(AnnotatorProfile.categories),
            )
            .where(Account.id == account_id)
        ).unique().scalar_one_or_none()

    def get_all(self, organization_id: int | None = None) -> list[Account]:
        statement = (
            select(Account)
            .options(
                joinedload(Account.profile).joinedload(AnnotatorProfile.categories),
            )
            .order_by(Account.username)
        )
        if organization_id is not None:
            statement = statement.where(Account.organization_id == organization_id)
        return list(self._session.execute(statement).scalars().unique().all())

    def create(self, account: Account) -> Account:
        self._session.add(account)
        self._session.flush()
        return account

    def flush(self) -> None:
        self._session.flush()

    def delete(self, account: Account) -> None:
        self._session.delete(account)
        self._session.flush()


def _rows(result: Result[Any]) -> int:
    """Rows an UPDATE or DELETE matched."""
    return cast(CursorResult[Any], result).rowcount


class RefreshTokenRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, token: RefreshToken) -> RefreshToken:
        self._session.add(token)
        self._session.flush()
        return token

    def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        # populate_existing: the conditional UPDATEs below bypass the session, which may hold the row from before them.
        return self._session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def claim(self, token_hash: str, now: datetime) -> RefreshToken | None:
        """Revokes the token if it is not revoked yet and returns it; None if it is unknown or already revoked.

        One conditional UPDATE, so of two requests presenting the same token at once only one gets it: in PostgreSQL
        the second waits for the first's row lock and then finds the token revoked."""
        claimed = self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        if _rows(claimed) != 1:
            return None
        return self.get_by_hash(token_hash)

    def revoke(self, token_hash: str, now: datetime) -> bool:
        revoked = self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        return _rows(revoked) == 1

    def revoke_all(self, account_id: int, now: datetime) -> None:
        self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.account_id == account_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )

    def delete_expired(self, account_id: int, now: datetime) -> None:
        self._session.execute(
            delete(RefreshToken)
            .where(RefreshToken.account_id == account_id, RefreshToken.expires_at < now)
            .execution_options(synchronize_session=False)
        )
