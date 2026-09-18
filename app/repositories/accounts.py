from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.account import Account, RefreshToken
from app.models.profile import AnnotatorProfile


class AccountRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_username(self, username: str) -> Account | None:
        return self.session.execute(
            select(Account)
            .options(
                joinedload(Account.profile).joinedload(AnnotatorProfile.group),
                joinedload(Account.profile).joinedload(AnnotatorProfile.categories),
            )
            .where(Account.username == username)
        ).unique().scalar_one_or_none()

    def get_by_id(self, account_id: int) -> Account | None:
        return self.session.execute(
            select(Account)
            .options(
                joinedload(Account.profile).joinedload(AnnotatorProfile.group),
                joinedload(Account.profile).joinedload(AnnotatorProfile.categories),
            )
            .where(Account.id == account_id)
        ).unique().scalar_one_or_none()

    def get_all(self) -> list[Account]:
        return list(
            self.session.execute(
                select(Account)
                .options(
                    joinedload(Account.profile).joinedload(AnnotatorProfile.group),
                    joinedload(Account.profile).joinedload(AnnotatorProfile.categories),
                )
                .order_by(Account.username)
            ).scalars().unique().all()
        )

    def create(self, account: Account) -> Account:
        self.session.add(account)
        self.session.flush()
        return account

    def update(self) -> None:
        self.session.flush()

    def delete(self, account: Account) -> None:
        self.session.delete(account)
        self.session.flush()


class RefreshTokenRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, token: RefreshToken) -> RefreshToken:
        self.session.add(token)
        self.session.flush()
        return token

    def get_by_token(self, token: str) -> RefreshToken | None:
        return self.session.execute(select(RefreshToken).where(RefreshToken.token == token)).scalar_one_or_none()

    def update(self) -> None:
        self.session.flush()
