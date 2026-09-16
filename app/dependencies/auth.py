from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.dependencies.resources import ResourcesDep, SessionDep
from app.models.account import Account, Role
from app.repositories.accounts import AccountRepository, RefreshTokenRepository
from app.repositories.profiles import ProfileRepository
from app.services.accounts import AccountService
from app.services.auth import AuthService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

def get_auth_service(session: SessionDep, resources: ResourcesDep) -> AuthService:
    return AuthService(
        resources.settings,
        AccountRepository(session),
        RefreshTokenRepository(session)
    )

def get_account_service(session: SessionDep, resources: ResourcesDep) -> AccountService:
    return AccountService(
        AccountRepository(session),
        ProfileRepository(session),
        get_auth_service(session, resources)
    )

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]


def get_current_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    auth_service: AuthServiceDep,
) -> Account:
    # Try getting token from authorization header first, then fallback to cookie
    actual_token = token or request.cookies.get("access_token")
    if not actual_token:
        # Check if the token is passed as "Bearer <token>" in a cookie sometimes happens
        if request.cookies.get("access_token", "").startswith("Bearer "):
            actual_token = request.cookies.get("access_token", "")[7:]
    
    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    user = auth_service.get_current_user_from_token(actual_token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUserDep = Annotated[Account, Depends(get_current_user)]


class RequireRole:
    def __init__(self, role: Role):
        self.role = role

    def __call__(self, current_user: CurrentUserDep) -> Account:
        if current_user.role != self.role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough privileges"
            )
        return current_user
