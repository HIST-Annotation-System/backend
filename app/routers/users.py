from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies.auth import AccountServiceDep, CurrentUserDep, RequireRole
from app.models.account import Role
from app.schemas.user import AccountCreate, AccountResponse, ProfileUpdate

router = APIRouter()

RequireAdmin = Annotated[CurrentUserDep, Depends(RequireRole(Role.ADMIN))]


@router.post("/", response_model=AccountResponse)
def create_account(request: AccountCreate, current_admin: RequireAdmin, account_service: AccountServiceDep) -> AccountResponse:
    # Admin creates an account for an annotator
    account = account_service.create_account(request)
    return account


@router.put("/me", response_model=AccountResponse)
def update_my_profile(request: ProfileUpdate, current_user: CurrentUserDep, account_service: AccountServiceDep) -> AccountResponse:
    is_admin = current_user.role == Role.ADMIN
    account = account_service.update_profile(current_user.id, current_user.id, is_admin, request)
    return account


@router.delete("/me")
def delete_my_account(current_user: CurrentUserDep, account_service: AccountServiceDep) -> dict[str, str]:
    account_service.delete_account(current_user.id, current_user.id, is_admin=current_user.role == Role.ADMIN)
    return {"status": "ok"}


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int, 
    request: ProfileUpdate, 
    current_admin: RequireAdmin, 
    account_service: AccountServiceDep
) -> AccountResponse:
    account = account_service.update_profile(current_admin.id, account_id, is_admin=True, data=request)
    return account


@router.delete("/{account_id}")
def delete_account(account_id: int, current_admin: RequireAdmin, account_service: AccountServiceDep) -> dict[str, str]:
    account_service.delete_account(current_admin.id, account_id, is_admin=True)
    return {"status": "ok"}
