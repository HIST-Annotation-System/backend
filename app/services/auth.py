import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import fastapi
from jose import JWTError, jwt

from app.core.config import Settings
from app.core.constants import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE
from app.models.account import Account, RefreshToken
from app.repositories.accounts import AccountRepository, RefreshTokenRepository


class AuthService:
    def __init__(self, settings: Settings, account_repo: AccountRepository, refresh_token_repo: RefreshTokenRepository):
        self.settings = settings
        self.account_repo = account_repo
        self.refresh_token_repo = refresh_token_repo

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

    def get_password_hash(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def create_access_token(self, subject: int, role: str) -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=self.settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode = {"exp": expire, "sub": str(subject), "role": role}
        encoded_jwt = jwt.encode(to_encode, self.settings.JWT_SECRET, algorithm=self.settings.JWT_ALGORITHM)
        return encoded_jwt

    def create_refresh_token(self, account_id: int) -> RefreshToken:
        token_str = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(days=self.settings.REFRESH_TOKEN_EXPIRE_DAYS)
        token = RefreshToken(account_id=account_id, token=token_str, expires_at=expires_at)
        return self.refresh_token_repo.create(token)

    def resolve_refresh_token(self, token_str: str) -> Account | None:
        token = self.refresh_token_repo.get_by_token(token_str)
        if not token:
            return None
        if token.is_revoked:
            return None
        # Use timezone.utc for comparison
        if token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            return None
            
        account = self.account_repo.get_by_id(token.account_id)
        return account

    def revoke_refresh_token(self, token_str: str) -> bool:
        token = self.refresh_token_repo.get_by_token(token_str)
        if token:
            token.is_revoked = True
            self.refresh_token_repo.flush()
            return True
        return False

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

    def set_token_cookies(self, response: "fastapi.Response", access_token: str, refresh_token: str | None = None) -> None:
        # TODO: change samesite on prod to 'lax' or 'strict' if they share the same domain.
        # Currently using 'none' + secure=True for cross-domain requests, 
        # or 'lax' if it's local development without HTTPS. Let's use lax for local dev compatibility.
        cookie_kwargs = {
            "httponly": True,
            "samesite": "lax",  # TODO: switch to strict/lax for prod, or none+secure if APIs are on different domains
            "secure": False,    # TODO: set to True in prod when using HTTPS
        }
        access_max_age = int(timedelta(minutes=self.settings.ACCESS_TOKEN_EXPIRE_MINUTES).total_seconds())
        response.set_cookie(key=ACCESS_TOKEN_COOKIE, value=access_token, max_age=access_max_age, **cookie_kwargs)
        if refresh_token:
            refresh_max_age = int(timedelta(days=self.settings.REFRESH_TOKEN_EXPIRE_DAYS).total_seconds())
            response.set_cookie(key=REFRESH_TOKEN_COOKIE, value=refresh_token, max_age=refresh_max_age, **cookie_kwargs)
