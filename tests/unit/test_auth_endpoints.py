"""The auth endpoints end to end: a FastAPI app with the real routers, over the unit tests' SQLite database."""

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.dependencies.resources import get_resources
from app.exceptions.errors import register_exception_handlers
from app.models import Account, Role
from app.models.account import RefreshToken
from app.routers import auth, users
from app.services.auth import REFRESH_REUSE_GRACE
from app.services.login_throttle import LoginThrottle
from conftest import Factory

PASSWORD = "correct horse battery"


@pytest.fixture
def throttle() -> LoginThrottle:
    return LoginThrottle(max_failures=3)


@pytest.fixture
def client(
    session_factory: sessionmaker[Session], settings: Settings, throttle: LoginThrottle
) -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth.router, prefix="/api/v1/auth")
    app.include_router(users.router, prefix="/api/v1/users")
    resources = SimpleNamespace(settings=settings, session_factory=session_factory, login_throttle=throttle)
    app.dependency_overrides[get_resources] = lambda: resources
    with TestClient(app) as client:
        yield client


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


def make_account(db: Session, *, first_login: bool = False, role: Role = Role.ADMIN) -> Account:
    factory = Factory(db)
    account = factory.account(
        role,
        None if role == Role.ADMIN else factory.organization(),
        hashed_password=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
        is_first_login=first_login,
    )
    db.commit()
    return account


def sign_in(client: TestClient, account: Account, password: str = PASSWORD) -> int:
    return client.post("/api/v1/auth/login", json={"username": account.username, "password": password}).status_code


class TestLogin:
    def test_sets_cookies_and_keeps_the_refresh_token_out_of_the_body(self, client: TestClient, db: Session) -> None:
        account = make_account(db)
        response = client.post("/api/v1/auth/login", json={"username": account.username, "password": PASSWORD})
        assert response.status_code == 200
        assert "refresh_token" not in response.json()
        assert response.json()["access_token"]
        assert {cookie.name: cookie.path for cookie in client.cookies.jar} == {
            "access_token": "/",
            "refresh_token": "/api/v1/auth",
        }
        assert client.get("/api/v1/users/me").json()["id"] == account.id

    def test_wrong_password_and_unknown_user_answer_alike(self, client: TestClient, db: Session) -> None:
        account = make_account(db)
        wrong = client.post("/api/v1/auth/login", json={"username": account.username, "password": "nope"})
        unknown = client.post("/api/v1/auth/login", json={"username": "nobody", "password": "nope"})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    def test_too_many_failures_lock_the_username_even_for_the_right_password(
        self, client: TestClient, db: Session
    ) -> None:
        account, other = make_account(db), make_account(db)
        for _ in range(3):
            assert sign_in(client, account, "nope") == 401
        response = client.post("/api/v1/auth/login", json={"username": account.username, "password": PASSWORD})
        assert response.status_code == 429
        assert response.json()["code"] == "too_many_login_attempts"
        assert int(response.headers["Retry-After"]) > 0
        assert sign_in(client, other) == 200

    def test_success_clears_the_failures(self, client: TestClient, db: Session) -> None:
        account = make_account(db)
        for _ in range(2):
            sign_in(client, account, "nope")
        assert sign_in(client, account) == 200
        for _ in range(2):
            sign_in(client, account, "nope")
        assert sign_in(client, account) == 200


class TestRefresh:
    def test_rotates_both_cookies(self, client: TestClient, db: Session) -> None:
        sign_in(client, make_account(db))
        before = client.cookies.get("refresh_token", path="/api/v1/auth")
        response = client.post("/api/v1/auth/refresh")
        assert response.status_code == 200
        assert "refresh_token" not in response.json()
        after = client.cookies.get("refresh_token", path="/api/v1/auth")
        assert after and after != before

    def test_without_a_cookie(self, client: TestClient) -> None:
        assert client.post("/api/v1/auth/refresh").status_code == 401

    def test_reuse_ends_every_session_and_the_revocation_is_kept(self, client: TestClient, db: Session) -> None:
        account = make_account(db)
        sign_in(client, account)
        stolen = client.cookies.get("refresh_token", path="/api/v1/auth")
        assert client.post("/api/v1/auth/refresh").status_code == 200
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.revoked_at.is_not(None))
            .values(revoked_at=datetime.now(UTC) - REFRESH_REUSE_GRACE * 2)
        )
        db.commit()

        thief = TestClient(client.app)
        thief.cookies.set("refresh_token", stolen, path="/api/v1/auth")
        assert thief.post("/api/v1/auth/refresh").status_code == 401
        # The user's own, current session was ended too: the failed request's revocation was committed.
        assert client.post("/api/v1/auth/refresh").status_code == 401
        db.expire_all()
        assert all(token.revoked_at is not None for token in db.execute(select(RefreshToken)).scalars())


class TestFirstLogin:
    def test_temporary_password_only_reaches_setup(self, client: TestClient, db: Session) -> None:
        account = make_account(db, first_login=True)
        response = client.post("/api/v1/auth/login", json={"username": account.username, "password": PASSWORD})
        assert response.json()["is_first_login"] is True
        assert client.cookies.get("refresh_token", path="/api/v1/auth") is None
        assert client.get("/api/v1/users/me").json()["is_first_login"] is True
        assert client.get("/api/v1/users/").status_code == 403

        setup = {"new_password": "a brand new password", "first_name": "A", "last_name": "B"}
        assert client.post("/api/v1/auth/setup-profile", json=setup).status_code == 200
        assert client.get("/api/v1/users/").status_code == 200
        assert client.post("/api/v1/auth/refresh").status_code == 200


class TestPasswordChange:
    def test_ends_other_sessions_and_keeps_the_current_one(self, client: TestClient, db: Session) -> None:
        account = make_account(db)
        other_device = TestClient(client.app)
        sign_in(other_device, account)
        sign_in(client, account)

        response = client.put(f"/api/v1/users/{account.id}", json={"password": "another long password"})
        assert response.status_code == 200
        assert client.post("/api/v1/auth/refresh").status_code == 200
        assert other_device.post("/api/v1/auth/refresh").status_code == 401

    def test_an_admin_resetting_a_password_ends_that_users_sessions(self, client: TestClient, db: Session) -> None:
        admin, user = make_account(db), make_account(db, role=Role.ANNOTATOR)
        users_browser = TestClient(client.app)
        sign_in(users_browser, user)
        sign_in(client, admin)
        assert client.put(f"/api/v1/users/{user.id}", json={"password": "reset by the admin"}).status_code == 200
        assert users_browser.post("/api/v1/auth/refresh").status_code == 401
        assert client.post("/api/v1/auth/refresh").status_code == 200


class TestLogout:
    def test_revokes_and_clears_even_without_an_access_token(self, client: TestClient, db: Session) -> None:
        sign_in(client, make_account(db))
        refresh = client.cookies.get("refresh_token", path="/api/v1/auth")
        client.cookies.delete("access_token")
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert not list(client.cookies.jar)

        copy = TestClient(client.app)
        copy.cookies.set("refresh_token", refresh, path="/api/v1/auth")
        assert copy.post("/api/v1/auth/refresh").status_code == 401
