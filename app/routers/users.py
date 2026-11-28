from fastapi import APIRouter, Response

from app.dependencies.auth import (
    AccountServiceDep,
    AuthenticatedUserDep,
    AuthServiceDep,
    CurrentUserDep,
    ManagerDep,
    OrgScopeDep,
)
from app.dependencies.resources import SessionDep
from app.dependencies.services import OrganizationServiceDep
from app.models.account import Account, Role
from app.schemas.user import AccountCreate, AccountResponse, ProfileUpdate

router = APIRouter()


@router.get("/", response_model=list[AccountResponse])
def get_all_accounts(_admin: ManagerDep, scope: OrgScopeDep, account_service: AccountServiceDep) -> list[Account]:
    return account_service.list_accounts(scope)


@router.get("/me", response_model=AccountResponse)
def get_current_user_profile(current_user: AuthenticatedUserDep) -> Account:
    # Also for an account still on its temporary password: the frontend reads `is_first_login` here to send it to setup.
    return current_user


@router.post("/", response_model=AccountResponse)
def create_account(
    request: AccountCreate,
    current_admin: ManagerDep,
    account_service: AccountServiceDep,
    organizations: OrganizationServiceDep,
    session: SessionDep
) -> Account:
    organization_id = (
        None if request.role == Role.ADMIN else organizations.target_for(current_admin, request.organization_id)
    )
    account = account_service.create_account(current_admin, request, organization_id)
    session.commit()
    return account


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int,
    request: ProfileUpdate,
    response: Response,
    current_user: CurrentUserDep,
    account_service: AccountServiceDep,
    auth_service: AuthServiceDep,
    organizations: OrganizationServiceDep,
    session: SessionDep
) -> Account:
    if request.organization_id is not None and current_user.role == Role.ADMIN:
        organizations.require(request.organization_id)
    account = account_service.update_profile(current_user, account_id, request)
    if request.password and account.id == current_user.id:
        # A new password ended every session of the account (see `update_profile`); this one goes on, in a new one.
        refresh_token = auth_service.create_refresh_token(account.id)
        session.commit()
        auth_service.set_token_cookies(response, auth_service.create_access_token(account.id, account.role.value), refresh_token)
        return account
    session.commit()
    return account


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    current_admin: ManagerDep,
    account_service: AccountServiceDep,
    session: SessionDep
) -> dict[str, str]:
    account_service.delete_account(current_admin, account_id)
    session.commit()
    return {"status": "ok"}
