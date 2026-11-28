import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.constants import REFRESH_TOKEN_COOKIE
from app.dependencies.auth import AccountServiceDep, AuthenticatedUserDep, AuthServiceDep, LoginThrottleDep
from app.dependencies.resources import SessionDep
from app.exceptions.errors import TooManyRequestsError
from app.models.account import Account
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.organizations import OrganizationRef
from app.schemas.user import ProfileSetup
from app.services.auth import RefreshOutcome

logger = logging.getLogger("app.auth")

router = APIRouter()

TOO_MANY_LOGIN_ATTEMPTS = "too_many_login_attempts"


def _who(account: Account) -> dict[str, Any]:
    organization = OrganizationRef.model_validate(account.organization) if account.organization else None
    return {"role": account.role, "organization": organization}


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    response: Response,
    auth_service: AuthServiceDep,
    throttle: LoginThrottleDep,
    session: SessionDep,
) -> TokenResponse:
    retry_after = throttle.retry_after(request.username)
    if retry_after is not None:
        raise TooManyRequestsError("Too many failed sign-in attempts, try again later", retry_after, TOO_MANY_LOGIN_ATTEMPTS)

    account = auth_service.authenticate(request.username, request.password)
    if account is None:
        throttle.record_failure(request.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    throttle.record_success(request.username)

    access_token = auth_service.create_access_token(account.id, account.role.value)
    if account.is_first_login:
        # No refresh token: the temporary password gets one short session, enough to set up the profile.
        auth_service.set_token_cookies(response, access_token)
        return TokenResponse(access_token=access_token, is_first_login=True, **_who(account))

    refresh_token = auth_service.create_refresh_token(account.id)
    # Committed before the cookies go out, so the client never holds a token the database doesn't.
    session.commit()
    auth_service.set_token_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, **_who(account))


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(request: Request, response: Response, auth_service: AuthServiceDep, session: SessionDep) -> TokenResponse:
    refresh_token_str = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not refresh_token_str:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    result = auth_service.rotate_refresh_token(refresh_token_str)
    if result.outcome is RefreshOutcome.REUSED:
        # Committed although the request fails: the revocation of the account's sessions must stay.
        session.commit()
        logger.warning("refresh token reused: every session of the account was ended")
    if result.outcome is not RefreshOutcome.ROTATED or result.account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    account = result.account
    access_token = auth_service.create_access_token(account.id, account.role.value)
    new_refresh_token = auth_service.create_refresh_token(account.id)
    # Committed before the cookies go out: a failed commit must not leave the client with a token the database lacks,
    # its old one already spent.
    session.commit()
    auth_service.set_token_cookies(response, access_token, new_refresh_token)
    return TokenResponse(access_token=access_token, **_who(account))


@router.post("/setup-profile", response_model=TokenResponse)
def setup_profile(
    request: ProfileSetup,
    response: Response,
    current_user: AuthenticatedUserDep,
    account_service: AccountServiceDep,
    auth_service: AuthServiceDep,
    session: SessionDep
) -> TokenResponse:
    if not current_user.is_first_login:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Profile already set up")

    account = account_service.setup_profile(current_user.id, request)
    access_token = auth_service.create_access_token(account.id, account.role.value)
    refresh_token = auth_service.create_refresh_token(account.id)
    session.commit()

    auth_service.set_token_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, **_who(account))


@router.post("/logout")
def logout(request: Request, response: Response, auth_service: AuthServiceDep, session: SessionDep) -> dict[str, str]:
    # No access token needed: signing out must work after it expired too. Revoked, so a copy of the cookie can't sign
    # in again after the user signed out.
    refresh_token_str = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if refresh_token_str:
        auth_service.revoke_refresh_token(refresh_token_str)
        session.commit()
    auth_service.clear_token_cookies(response)
    return {"status": "ok"}
