import sys
from sqlalchemy import select
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from app.core.config import settings
from app.dependencies.resources import build_resources
from app.models.account import Account, Role

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def main():
    resources = build_resources(settings)
    session = resources.session_factory()

    try:
        # Check if admin already exists
        admin = session.execute(select(Account).where(Account.username == "admin")).scalar_one_or_none()
        if admin:
            print("Admin account already exists.")
            sys.exit(0)

        password = settings.ADMIN_PASSWORD
        hashed_password = pwd_context.hash(password)

        new_admin = Account(
            username="admin",
            hashed_password=hashed_password,
            role=Role.ADMIN,
            is_first_login=False
        )
        session.add(new_admin)
        session.commit()
        print("Admin account created successfully with username 'admin'.")

    except Exception as e:
        session.rollback()
        print(f"Error creating admin account: {e}")
        sys.exit(1)
    finally:
        session.close()
        resources.close()

if __name__ == "__main__":
    main()
