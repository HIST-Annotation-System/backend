from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.core.constants import ACCESS_TOKEN_COOKIE
from app.dependencies.resources import ResourcesDep, SessionDep
from app.models.account import Account, Role
from app.repositories.accounts import AccountRepository, RefreshTokenRepository
from app.repositories.profiles import ProfileRepository
from app.services.accounts import AccountService
from app.services.auth import AuthService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login", auto_error=False)

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
    bearer_or_cookie_token = token or request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not bearer_or_cookie_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = auth_service.get_current_user_from_token(bearer_or_cookie_token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUserDep = Annotated[Account, Depends(get_current_user)]


class RequireRole:
    def __init__(self, *roles: Role):
        self._roles = roles

    def __call__(self, current_user: CurrentUserDep) -> Account:
        if current_user.role not in self._roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough privileges"
            )
        return current_user


require_admin = RequireRole(Role.ADMIN)
AdminDep = Annotated[Account, Depends(require_admin)]
require_manager = RequireRole(Role.ADMIN, Role.ORG_ADMIN)
ManagerDep = Annotated[Account, Depends(require_manager)]


def get_org_scope(current_user: CurrentUserDep) -> int | None:
    """The only organization whose data the user may see, or None for the system admin (sees all of them)."""
    if current_user.role == Role.ADMIN:
        return None
    if current_user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account has no organization")
    return current_user.organization_id


OrgScopeDep = Annotated[int | None, Depends(get_org_scope)]
