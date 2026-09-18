from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import AccountServiceDep, CurrentUserDep, RequireRole
from app.dependencies.resources import SessionDep
from app.models.account import Account, Role
from app.schemas.user import AccountCreate, AccountResponse, ProfileUpdate

router = APIRouter()

RequireAdmin = Annotated[CurrentUserDep, Depends(RequireRole(Role.ADMIN))]


@router.get("/", response_model=list[AccountResponse])
def get_all_accounts(current_admin: RequireAdmin, account_service: AccountServiceDep) -> list[Account]:
    return account_service.account_repo.get_all()


@router.get("/me", response_model=AccountResponse)
def get_current_user_profile(current_user: CurrentUserDep) -> Account:
    return current_user


@router.post("/", response_model=AccountResponse)
def create_account(
    request: AccountCreate, 
    current_admin: RequireAdmin, 
    account_service: AccountServiceDep,
    session: SessionDep
) -> AccountResponse:
    # Admin creates an account for an annotator
    account = account_service.create_account(request)
    session.commit()
    return account


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int, 
    request: ProfileUpdate, 
    current_user: CurrentUserDep, 
    account_service: AccountServiceDep,
    session: SessionDep
) -> AccountResponse:
    is_admin = current_user.role == Role.ADMIN
    if not is_admin and current_user.id != account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough privileges to edit this account")

    account = account_service.update_profile(current_user.id, account_id, is_admin=is_admin, data=request)
    session.commit()
    return account


@router.delete("/{account_id}")
def delete_account(
    account_id: int, 
    current_admin: RequireAdmin, 
    account_service: AccountServiceDep,
    session: SessionDep
) -> dict[str, str]:
    account_service.delete_account(current_admin.id, account_id, is_admin=True)
    session.commit()
    return {"status": "ok"}
