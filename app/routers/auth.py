from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.dependencies.auth import AccountServiceDep, AuthServiceDep, CurrentUserDep
from app.schemas.auth import LoginRequest, RefreshTokenRequest, TokenResponse
from app.schemas.user import ProfileSetup

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, response: Response, auth_service: AuthServiceDep, account_service: AccountServiceDep) -> TokenResponse:
    account = account_service.account_repo.get_by_username(request.username)
    if not account or not auth_service.verify_password(request.password, account.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if account.is_first_login:
        access_token = auth_service.create_access_token(account.id, account.role.value)
        auth_service.set_token_cookies(response, access_token)
        return TokenResponse(access_token=access_token, is_first_login=True)

    access_token = auth_service.create_access_token(account.id, account.role.value)
    refresh_token_obj = auth_service.create_refresh_token(account.id)
    
    auth_service.set_token_cookies(response, access_token, refresh_token_obj.token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token_obj.token)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(request: Request, response: Response, auth_service: AuthServiceDep) -> TokenResponse:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
        
    account = auth_service.verify_refresh_token(refresh_token)
    if not account:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    auth_service.revoke_refresh_token(refresh_token)
    
    access_token = auth_service.create_access_token(account.id, account.role.value)
    new_refresh_token_obj = auth_service.create_refresh_token(account.id)
    
    auth_service.set_token_cookies(response, access_token, new_refresh_token_obj.token)
    return TokenResponse(access_token=access_token, refresh_token=new_refresh_token_obj.token)


@router.post("/setup-profile", response_model=TokenResponse)
def setup_profile(
    request: ProfileSetup, 
    response: Response, 
    current_user: CurrentUserDep, 
    account_service: AccountServiceDep,
    auth_service: AuthServiceDep
) -> TokenResponse:
    if not current_user.is_first_login:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Profile already set up")
        
    account = account_service.setup_profile(current_user.id, request)
    
    access_token = auth_service.create_access_token(account.id, account.role.value)
    refresh_token_obj = auth_service.create_refresh_token(account.id)
    
    auth_service.set_token_cookies(response, access_token, refresh_token_obj.token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token_obj.token)


@router.post("/logout")
def logout(response: Response, current_user: CurrentUserDep) -> dict[str, str]:
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    # In a full implementation, we would also revoke the refresh token if passed.
    return {"status": "ok"}
