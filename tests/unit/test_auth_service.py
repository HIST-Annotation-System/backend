from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response
from jose import jwt
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Role
from app.repositories.accounts import AccountRepository, RefreshTokenRepository
from app.services.auth import AuthService
from conftest import Factory, make_settings


@pytest.fixture
def auth(session: Session, settings: Settings) -> AuthService:
    return AuthService(settings, AccountRepository(session), RefreshTokenRepository(session))


class TestPasswords:
    def test_hash_verifies_only_the_right_password(self, auth: AuthService) -> None:
        hashed = auth.get_password_hash("correct horse")
        assert hashed != "correct horse"
        assert auth.verify_password("correct horse", hashed)
        assert not auth.verify_password("wrong", hashed)

    def test_same_password_hashes_differently(self, auth: AuthService) -> None:
        assert auth.get_password_hash("pw") != auth.get_password_hash("pw")

    def test_non_ascii_passwords(self, auth: AuthService) -> None:
        assert auth.verify_password("zażółć", auth.get_password_hash("zażółć"))


class TestAccessTokens:
    def test_round_trip(self, auth: AuthService, factory: Factory) -> None:
        account = factory.account(Role.ORG_ADMIN, factory.organization())
        token = auth.create_access_token(account.id, account.role.value)
        assert auth.get_current_user_from_token(token) is account

    def test_token_carries_subject_role_and_expiry(self, auth: AuthService, settings: Settings) -> None:
        payload = jwt.decode(auth.create_access_token(7, "ADMIN"), settings.JWT_SECRET, algorithms=["HS256"])
        assert payload["sub"] == "7" and payload["role"] == "ADMIN"
        expected = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        assert abs(payload["exp"] - expected.timestamp()) < 5

    def test_expired_token_is_rejected(self, session: Session, factory: Factory) -> None:
        account = factory.account()
        expired = AuthService(
            make_settings(ACCESS_TOKEN_EXPIRE_MINUTES=-1), AccountRepository(session), RefreshTokenRepository(session)
        )
        assert expired.get_current_user_from_token(expired.create_access_token(account.id, "ANNOTATOR")) is None

    def test_token_signed_with_another_secret_is_rejected(self, auth: AuthService, factory: Factory) -> None:
        account = factory.account()
        forged = jwt.encode({"sub": str(account.id)}, "attacker-secret", algorithm="HS256")
        assert auth.get_current_user_from_token(forged) is None

    @pytest.mark.parametrize("claims", [{}, {"sub": "not-a-number"}])
    def test_token_without_a_usable_subject_is_rejected(
        self, auth: AuthService, settings: Settings, claims: dict[str, str]
    ) -> None:
        assert auth.get_current_user_from_token(jwt.encode(claims, settings.JWT_SECRET, algorithm="HS256")) is None

    def test_garbage_is_rejected(self, auth: AuthService) -> None:
        assert auth.get_current_user_from_token("not.a.jwt") is None

    def test_token_of_a_deleted_account_resolves_to_nobody(self, auth: AuthService) -> None:
        assert auth.get_current_user_from_token(auth.create_access_token(999, "ANNOTATOR")) is None


class TestRefreshTokens:
    def test_create_resolve_revoke(self, auth: AuthService, factory: Factory) -> None:
        account = factory.account()
        token = auth.create_refresh_token(account.id)
        assert len(token.token) >= 32
        assert auth.resolve_refresh_token(token.token) is account
        assert auth.revoke_refresh_token(token.token) is True
        assert auth.resolve_refresh_token(token.token) is None

    def test_unknown_token(self, auth: AuthService) -> None:
        assert auth.resolve_refresh_token("nope") is None
        assert auth.revoke_refresh_token("nope") is False

    def test_expired_token(self, auth: AuthService, factory: Factory, session: Session) -> None:
        token = auth.create_refresh_token(factory.account().id)
        token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.flush()
        assert auth.resolve_refresh_token(token.token) is None

    def test_tokens_are_unique(self, auth: AuthService, factory: Factory) -> None:
        account = factory.account()
        assert auth.create_refresh_token(account.id).token != auth.create_refresh_token(account.id).token


class TestCookies:
    def cookies(self, response: Response) -> dict[str, str]:
        headers = [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]
        return {header.split("=", 1)[0]: header for header in headers}

    def test_access_and_refresh_cookies_are_http_only(self, auth: AuthService, settings: Settings) -> None:
        response = Response()
        auth.set_token_cookies(response, "acc", "ref")
        cookies = self.cookies(response)
        assert set(cookies) == {"access_token", "refresh_token"}
        for cookie in cookies.values():
            assert "HttpOnly" in cookie
            assert "SameSite=lax" in cookie
        assert f"Max-Age={settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60}" in cookies["access_token"]
        assert f"Max-Age={settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400}" in cookies["refresh_token"]

    def test_refresh_cookie_is_optional(self, auth: AuthService) -> None:
        response = Response()
        auth.set_token_cookies(response, "acc")
        assert set(self.cookies(response)) == {"access_token"}
