from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response
from jose import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Role
from app.repositories.accounts import AccountRepository, RefreshTokenRepository
from app.models.account import RefreshToken
from app.services.auth import REFRESH_REUSE_GRACE, AuthService, RefreshOutcome, hash_refresh_token
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
    def test_only_a_hash_of_the_token_is_stored(self, auth: AuthService, factory: Factory, session: Session) -> None:
        token = auth.create_refresh_token(factory.account().id)
        assert len(token) >= 32
        stored = session.execute(select(RefreshToken)).scalar_one()
        assert stored.token_hash == hash_refresh_token(token) != token

    def test_rotation_spends_the_token(self, auth: AuthService, factory: Factory) -> None:
        account = factory.account()
        token = auth.create_refresh_token(account.id)
        result = auth.rotate_refresh_token(token)
        assert result.outcome is RefreshOutcome.ROTATED and result.account is account
        # Presented again at once: a race between two tabs, refused without ending the account's other sessions.
        other = auth.create_refresh_token(account.id)
        assert auth.rotate_refresh_token(token).outcome is RefreshOutcome.INVALID
        assert auth.rotate_refresh_token(other).outcome is RefreshOutcome.ROTATED

    def test_reuse_after_the_grace_period_ends_every_session(
        self, auth: AuthService, factory: Factory, session: Session
    ) -> None:
        account = factory.account()
        stolen = auth.create_refresh_token(account.id)
        assert auth.rotate_refresh_token(stolen).outcome is RefreshOutcome.ROTATED
        current = auth.create_refresh_token(account.id)
        spent = session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(stolen))
        ).scalar_one()
        spent.revoked_at = datetime.now(UTC) - REFRESH_REUSE_GRACE - timedelta(seconds=1)
        session.flush()

        assert auth.rotate_refresh_token(stolen).outcome is RefreshOutcome.REUSED
        assert auth.rotate_refresh_token(current).outcome is RefreshOutcome.INVALID

    def test_reuse_only_ends_the_sessions_of_that_account(
        self, auth: AuthService, factory: Factory, session: Session
    ) -> None:
        victim, bystander = factory.account(), factory.account()
        stolen = auth.create_refresh_token(victim.id)
        auth.rotate_refresh_token(stolen)
        theirs = auth.create_refresh_token(bystander.id)
        for row in session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_not(None))).scalars():
            row.revoked_at = datetime.now(UTC) - REFRESH_REUSE_GRACE * 2
        session.flush()
        assert auth.rotate_refresh_token(stolen).outcome is RefreshOutcome.REUSED
        assert auth.rotate_refresh_token(theirs).outcome is RefreshOutcome.ROTATED

    def test_unknown_token(self, auth: AuthService) -> None:
        assert auth.rotate_refresh_token("nope").outcome is RefreshOutcome.INVALID
        assert auth.revoke_refresh_token("nope") is False

    def test_expired_token(self, auth: AuthService, factory: Factory, session: Session) -> None:
        token = auth.create_refresh_token(factory.account().id)
        stored = session.execute(select(RefreshToken)).scalar_one()
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.flush()
        assert auth.rotate_refresh_token(token).outcome is RefreshOutcome.INVALID

    def test_revoked_token_no_longer_rotates(self, auth: AuthService, factory: Factory) -> None:
        token = auth.create_refresh_token(factory.account().id)
        assert auth.revoke_refresh_token(token) is True
        assert auth.revoke_refresh_token(token) is False
        assert auth.rotate_refresh_token(token).outcome is RefreshOutcome.INVALID

    def test_revoke_all(self, auth: AuthService, factory: Factory) -> None:
        account = factory.account()
        tokens = [auth.create_refresh_token(account.id) for _ in range(2)]
        auth.revoke_all_refresh_tokens(account.id)
        assert all(auth.rotate_refresh_token(token).outcome is RefreshOutcome.INVALID for token in tokens)

    def test_new_session_deletes_expired_tokens(self, auth: AuthService, factory: Factory, session: Session) -> None:
        account = factory.account()
        auth.create_refresh_token(account.id)
        stored = session.execute(select(RefreshToken)).scalar_one()
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.flush()
        auth.create_refresh_token(account.id)
        assert len(session.execute(select(RefreshToken)).scalars().all()) == 1

    def test_tokens_are_unique(self, auth: AuthService, factory: Factory) -> None:
        account = factory.account()
        assert auth.create_refresh_token(account.id) != auth.create_refresh_token(account.id)


class TestAuthenticate:
    def test_right_and_wrong_password(self, auth: AuthService, factory: Factory) -> None:
        account = factory.account(hashed_password=auth.get_password_hash("pw"))
        assert auth.authenticate(account.username, "pw") is account
        assert auth.authenticate(account.username, "nope") is None

    def test_unknown_username_still_checks_a_password(
        self, auth: AuthService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checked: list[str] = []
        real = auth.verify_password

        def verify(plain: str, hashed: str) -> bool:
            checked.append(plain)
            return real(plain, hashed)

        monkeypatch.setattr(auth, "verify_password", verify)
        assert auth.authenticate("nobody", "pw") is None
        assert checked == ["pw"]


class TestCookies:
    def cookies(self, response: Response) -> dict[tuple[str, str], str]:
        """Set-Cookie headers by (name, path)."""
        headers = [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]
        found = {}
        for header in headers:
            path = next(part.split("=", 1)[1] for part in header.split("; ") if part.startswith("Path="))
            found[(header.split("=", 1)[0], path)] = header
        return found

    def test_access_and_refresh_cookies_are_http_only(self, auth: AuthService, settings: Settings) -> None:
        response = Response()
        auth.set_token_cookies(response, "acc", "ref")
        cookies = self.cookies(response)
        access, refresh = cookies[("access_token", "/")], cookies[("refresh_token", "/api/v1/auth")]
        for cookie in (access, refresh):
            assert "HttpOnly" in cookie
            assert "SameSite=lax" in cookie
        assert f"Max-Age={settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60}" in access
        assert f"Max-Age={settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400}" in refresh

    def test_a_refresh_cookie_left_on_the_old_path_is_removed(self, auth: AuthService) -> None:
        response = Response()
        auth.set_token_cookies(response, "acc", "ref")
        assert "Max-Age=0" in self.cookies(response)[("refresh_token", "/")]

    def test_cookies_are_https_only_where_configured(self, auth: AuthService, settings: Settings) -> None:
        keys = [("access_token", "/"), ("refresh_token", "/api/v1/auth")]
        response = Response()
        auth.set_token_cookies(response, "acc", "ref")
        cookies = self.cookies(response)
        assert all("Secure" not in cookies[key] for key in keys)
        settings.COOKIE_SECURE = True
        response = Response()
        auth.set_token_cookies(response, "acc", "ref")
        cookies = self.cookies(response)
        assert all("Secure" in cookies[key] for key in keys)

    def test_refresh_cookie_is_optional(self, auth: AuthService) -> None:
        response = Response()
        auth.set_token_cookies(response, "acc")
        assert set(self.cookies(response)) == {("access_token", "/")}

    def test_clearing_removes_every_auth_cookie(self, auth: AuthService) -> None:
        response = Response()
        auth.clear_token_cookies(response)
        cookies = self.cookies(response)
        assert set(cookies) == {("access_token", "/"), ("refresh_token", "/api/v1/auth"), ("refresh_token", "/")}
        assert all("Max-Age=0" in cookie for cookie in cookies.values())
