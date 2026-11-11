from app.exceptions.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.models.account import Account, Role
from app.models.profile import AnnotatorProfile
from app.repositories.accounts import AccountRepository
from app.repositories.profiles import ProfileRepository
from app.schemas.user import AccountCreate, ProfileSetup, ProfileUpdate
from app.services.auth import AuthService

ACCOUNT_NOT_FOUND = "Account not found."
USERNAME_TAKEN = "Username already registered."
PROFILE_ALREADY_SETUP = "Profile already setup."
CANNOT_EDIT_ANOTHER_ADMIN = "Cannot edit another admin."
CANNOT_DELETE_ANOTHER_ADMIN = "Cannot delete another admin."
CANNOT_ASSIGN_ROLE = "Not enough privileges to assign role {role}."
CANNOT_CHANGE_OWN_ROLE = "Cannot change your own role or organization."
CANNOT_CHANGE_ORGANIZATION = "Only the system administrator can move an account to another organization."
ORGANIZATION_REQUIRED = "Accounts other than system administrators need an organization."
# What an organization's admin may make of the accounts of their organization.
ORG_ADMIN_ASSIGNABLE_ROLES = (Role.ANNOTATOR, Role.ORG_ADMIN)


class AccountService:
    def __init__(self, account_repo: AccountRepository, profile_repo: ProfileRepository, auth_service: AuthService):
        self.account_repo = account_repo
        self.profile_repo = profile_repo
        self.auth_service = auth_service

    def get_account(self, account_id: int) -> Account:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise NotFoundError(ACCOUNT_NOT_FOUND)
        return account

    def list_accounts(self, organization_id: int | None) -> list[Account]:
        return self.account_repo.get_all(organization_id)

    def create_account(self, actor: Account, data: AccountCreate, organization_id: int | None) -> Account:
        self._require_assignable(actor, data.role)
        if data.role == Role.ADMIN:
            organization_id = None
        elif organization_id is None:
            raise InvalidInputError(ORGANIZATION_REQUIRED)
        if self.account_repo.get_by_username(data.username):
            raise ConflictError(USERNAME_TAKEN)

        hashed_password = self.auth_service.get_password_hash(data.password)
        account = Account(
            username=data.username,
            hashed_password=hashed_password,
            role=data.role,
            is_first_login=True,
            organization_id=organization_id,
        )
        self.account_repo.create(account)

        # Create an empty profile immediately so admin can set can_upload
        profile = AnnotatorProfile(
            account_id=account.id,
            first_name="",
            last_name="",
            can_upload=data.can_upload,
        )
        self.profile_repo.create(profile)
        self.account_repo.flush()

        return account

    def setup_profile(self, account_id: int, data: ProfileSetup) -> Account:
        account = self.get_account(account_id)
        if not account.is_first_login:
            raise ConflictError(PROFILE_ALREADY_SETUP)

        account.hashed_password = self.auth_service.get_password_hash(data.new_password)
        account.is_first_login = False

        if not account.profile:
            # Fallback if profile wasn't created (e.g. older users)
            account.profile = AnnotatorProfile(
                account_id=account.id,
                first_name=data.first_name,
                last_name=data.last_name,
                can_upload=False,
            )
            self.profile_repo.create(account.profile)
        else:
            account.profile.first_name = data.first_name
            account.profile.last_name = data.last_name

        categories = self.profile_repo.get_categories_by_ids(data.category_ids)
        account.profile.categories = categories

        self.account_repo.flush()
        return account

    def update_profile(self, actor: Account, target_account_id: int, data: ProfileUpdate) -> Account:
        """Annotators edit only themselves; administrators edit the accounts they manage (see `_require_managed`),
        including the role, and the system admin the organization."""
        target_account = self.get_account(target_account_id)
        is_admin = actor.role in (Role.ADMIN, Role.ORG_ADMIN)
        if is_admin:
            self._require_managed(actor, target_account, CANNOT_EDIT_ANOTHER_ADMIN)
        elif target_account.id != actor.id:
            raise ForbiddenError(CANNOT_EDIT_ANOTHER_ADMIN)

        if is_admin:
            self._change_role_and_organization(actor, target_account, data)

        if data.password:
            target_account.hashed_password = self.auth_service.get_password_hash(data.password)

        if target_account.profile:
            if data.first_name is not None:
                target_account.profile.first_name = data.first_name
            if data.last_name is not None:
                target_account.profile.last_name = data.last_name
            if data.can_upload is not None and is_admin:
                target_account.profile.can_upload = data.can_upload
            if data.category_ids is not None:
                categories = self.profile_repo.get_categories_by_ids(data.category_ids)
                target_account.profile.categories = categories

        self.account_repo.flush()
        return target_account

    def delete_account(self, actor: Account, target_account_id: int) -> None:
        target_account = self.get_account(target_account_id)
        self._require_managed(actor, target_account, CANNOT_DELETE_ANOTHER_ADMIN)
        self.account_repo.delete(target_account)

    def _require_managed(self, actor: Account, target: Account, refusal: str) -> None:
        """The system admin manages every account but other system admins; an organization's admin manages the
        accounts of their organization but its other admins. Accounts of other organizations are not found."""
        if actor.role == Role.ORG_ADMIN and target.organization_id != actor.organization_id:
            raise NotFoundError(ACCOUNT_NOT_FOUND)
        if target.role == actor.role and target.id != actor.id:
            raise ForbiddenError(refusal)

    def _require_assignable(self, actor: Account, role: Role) -> None:
        if actor.role != Role.ADMIN and role not in ORG_ADMIN_ASSIGNABLE_ROLES:
            raise ForbiddenError(CANNOT_ASSIGN_ROLE.format(role=role.value))

    def _change_role_and_organization(self, actor: Account, target: Account, data: ProfileUpdate) -> None:
        moves = "organization_id" in data.model_fields_set and data.organization_id != target.organization_id
        role = data.role if data.role is not None else target.role
        if role == target.role and not moves:
            return
        if target.id == actor.id:
            raise ForbiddenError(CANNOT_CHANGE_OWN_ROLE)
        self._require_assignable(actor, role)
        if moves and actor.role != Role.ADMIN:
            raise ForbiddenError(CANNOT_CHANGE_ORGANIZATION)
        organization_id = data.organization_id if moves else target.organization_id
        if role == Role.ADMIN:
            organization_id = None
        elif organization_id is None:
            raise InvalidInputError(ORGANIZATION_REQUIRED)
        target.role, target.organization_id = role, organization_id
