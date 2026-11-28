import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import cache

import bcrypt
import fastapi
from jose import JWTError, jwt

from app.core.config import Settings
from app.core.constants import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE
from app.models.account import Account, RefreshToken
from app.repositories.accounts import AccountRepository, RefreshTokenRepository

# A token presented again this soon after it was rotated is taken as two requests racing with the same cookie (two
# tabs refreshing at once), not as theft: the late one is refused, but the account's sessions are left alone. Its tab
# then finds the new cookies the winner received, since tabs share them.
REFRESH_REUSE_GRACE = timedelta(seconds=60)


@cache
def _dummy_password_hash() -> str:
    """A hash to check passwords against when the username doesn't exist, so that answer takes as long as a wrong
    password and doesn't reveal which usernames exist. Computed once, at the default cost like real hashes."""
    return bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode("utf-8")


class RefreshOutcome(Enum):
    ROTATED = "rotated"
    INVALID = "invalid"
    # A token revoked a while ago came back: someone has a copy of it. Every session of the account was ended.
    REUSED = "reused"


@dataclass(frozen=True)
class RefreshResult:
    outcome: RefreshOutcome
    account: Account | None = None


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    # SQLite (the unit tests) returns naive datetimes; PostgreSQL returns aware ones.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class AuthService:
    def __init__(self, settings: Settings, account_repo: AccountRepository, refresh_token_repo: RefreshTokenRepository):
        self.settings = settings
        self.account_repo = account_repo
        self.refresh_token_repo = refresh_token_repo

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

    def get_password_hash(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def authenticate(self, username: str, password: str) -> Account | None:
        """The account with this username and password, or None. Takes as long for an unknown username."""
        account = self.account_repo.get_by_username(username)
        if account is None:
            self.verify_password(password, _dummy_password_hash())
            return None
        return account if self.verify_password(password, account.hashed_password) else None

    def create_access_token(self, subject: int, role: str) -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=self.settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode = {"exp": expire, "sub": str(subject), "role": role}
        return str(jwt.encode(to_encode, self.settings.JWT_SECRET, algorithm=self.settings.JWT_ALGORITHM))

    def create_refresh_token(self, account_id: int) -> str:
        """Starts a session: returns the new token, of which only the hash is stored. Also deletes the account's
        expired tokens, which have nothing left to do."""
        now = datetime.now(timezone.utc)
        self.refresh_token_repo.delete_expired(account_id, now)
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(days=self.settings.REFRESH_TOKEN_EXPIRE_DAYS)
        self.refresh_token_repo.create(
            RefreshToken(account_id=account_id, token_hash=hash_refresh_token(token), expires_at=expires_at)
        )
        return token

    def rotate_refresh_token(self, token: str) -> RefreshResult:
        """Spends the token: on success it is revoked and its account returned, and the caller issues a new one.

        Presenting a token that was revoked longer than `REFRESH_REUSE_GRACE` ago ends every session of the account:
        either the user or someone with a stolen copy used it first, and there is no telling which."""
        now = datetime.now(timezone.utc)
        token_hash = hash_refresh_token(token)
        claimed = self.refresh_token_repo.claim(token_hash, now)
        if claimed is not None:
            if _as_utc(claimed.expires_at) <= now:
                return RefreshResult(RefreshOutcome.INVALID)
            account = self.account_repo.get_by_id(claimed.account_id)
            return RefreshResult(RefreshOutcome.ROTATED, account) if account else RefreshResult(RefreshOutcome.INVALID)

        existing = self.refresh_token_repo.get_by_hash(token_hash)
        if existing is None or existing.revoked_at is None or _as_utc(existing.expires_at) <= now:
            return RefreshResult(RefreshOutcome.INVALID)
        if now - _as_utc(existing.revoked_at) <= REFRESH_REUSE_GRACE:
            return RefreshResult(RefreshOutcome.INVALID)
        self.refresh_token_repo.revoke_all(existing.account_id, now)
        return RefreshResult(RefreshOutcome.REUSED)

    def revoke_refresh_token(self, token: str) -> bool:
        return self.refresh_token_repo.revoke(hash_refresh_token(token), datetime.now(timezone.utc))

    def revoke_all_refresh_tokens(self, account_id: int) -> None:
        """Ends every session of the account (their access tokens still work until they expire)."""
        self.refresh_token_repo.revoke_all(account_id, datetime.now(timezone.utc))

    def get_current_user_from_token(self, token: str) -> Account | None:
        try:
            payload = jwt.decode(token, self.settings.JWT_SECRET, algorithms=[self.settings.JWT_ALGORITHM])
            account_id_str = payload.get("sub")
            if account_id_str is None:
                return None
            account_id = int(account_id_str)
        except (JWTError, ValueError):
            return None

        return self.account_repo.get_by_id(account_id)

    @property
    def refresh_cookie_path(self) -> str:
        # Only the auth endpoints read it, so the browser doesn't send it with every other request.
        return f"{self.settings.API_V1_STR}/auth"

    def set_token_cookies(self, response: "fastapi.Response", access_token: str, refresh_token: str | None = None) -> None:
        # The frontend and the API share one origin in prod (CloudFront) and one site locally (localhost), so lax works
        # in both; only prod is HTTPS.
        access_max_age = int(timedelta(minutes=self.settings.ACCESS_TOKEN_EXPIRE_MINUTES).total_seconds())
        self._set_cookie(response, ACCESS_TOKEN_COOKIE, access_token, access_max_age, "/")
        if refresh_token:
            refresh_max_age = int(timedelta(days=self.settings.REFRESH_TOKEN_EXPIRE_DAYS).total_seconds())
            self._set_cookie(response, REFRESH_TOKEN_COOKIE, refresh_token, refresh_max_age, self.refresh_cookie_path)
            self._delete_legacy_refresh_cookie(response)

    def _set_cookie(self, response: "fastapi.Response", key: str, value: str, max_age: int, path: str) -> None:
        response.set_cookie(
            key=key,
            value=value,
            max_age=max_age,
            path=path,
            httponly=True,
            samesite="lax",
            secure=self.settings.COOKIE_SECURE,
        )

    def clear_token_cookies(self, response: "fastapi.Response") -> None:
        response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
        response.delete_cookie(REFRESH_TOKEN_COOKIE, path=self.refresh_cookie_path)
        self._delete_legacy_refresh_cookie(response)

    @staticmethod
    def _delete_legacy_refresh_cookie(response: "fastapi.Response") -> None:
        # Refresh cookies used to be set on "/". A browser holding one would send it alongside the new one, and the
        # server would read the stale one: remove it whenever a new one is set.
        response.delete_cookie(REFRESH_TOKEN_COOKIE, path="/")
