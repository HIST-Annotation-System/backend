"""Creates the admin account (and its profile) if it isn't there yet."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account import Account, Role
from app.models.profile import AnnotatorProfile
from app.repositories.accounts import AccountRepository, RefreshTokenRepository
from app.services.auth import AuthService


def main() -> None:
    engine = create_engine(str(settings.DATABASE_URL))
    try:
        with Session(engine) as session:
            accounts = AccountRepository(session)
            admin = accounts.get_by_username(settings.ADMIN_USERNAME)
            if admin is None:
                auth_service = AuthService(settings, accounts, RefreshTokenRepository(session))
                admin = accounts.create(
                    Account(
                        username=settings.ADMIN_USERNAME,
                        hashed_password=auth_service.get_password_hash(settings.ADMIN_PASSWORD),
                        role=Role.ADMIN,
                        is_first_login=False,
                    )
                )
                print(f"Created the '{settings.ADMIN_USERNAME}' account (password: ADMIN_PASSWORD).")
            else:
                print(f"The '{settings.ADMIN_USERNAME}' account already exists.")
            if admin.profile is None:
                # Without a profile row, editing the account's profile silently saves nothing.
                session.add(
                    AnnotatorProfile(account_id=admin.id, first_name="", last_name="", can_upload=True)
                )
                print("Added the missing profile.")
            session.commit()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
