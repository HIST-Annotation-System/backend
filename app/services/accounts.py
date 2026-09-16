from fastapi import HTTPException, status

from app.models.account import Account, Role
from app.models.profile import AnnotatorProfile
from app.repositories.accounts import AccountRepository
from app.repositories.profiles import ProfileRepository
from app.schemas.user import AccountCreate, ProfileSetup, ProfileUpdate
from app.services.auth import AuthService


class AccountService:
    def __init__(self, account_repo: AccountRepository, profile_repo: ProfileRepository, auth_service: AuthService):
        self.account_repo = account_repo
        self.profile_repo = profile_repo
        self.auth_service = auth_service

    def get_account(self, account_id: int) -> Account:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        return account

    def create_account(self, data: AccountCreate) -> Account:
        if self.account_repo.get_by_username(data.username):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")
            
        hashed_password = self.auth_service.get_password_hash(data.password)
        account = Account(
            username=data.username,
            hashed_password=hashed_password,
            role=data.role,
            is_first_login=True
        )
        return self.account_repo.create(account)

    def setup_profile(self, account_id: int, data: ProfileSetup) -> Account:
        account = self.get_account(account_id)
        if not account.is_first_login:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Profile already setup")

        account.hashed_password = self.auth_service.get_password_hash(data.new_password)
        account.is_first_login = False

        profile = AnnotatorProfile(
            account_id=account.id,
            first_name=data.first_name,
            last_name=data.last_name,
            can_upload=False,
            group_id=data.group_id
        )
        
        categories = self.profile_repo.get_categories_by_ids(data.category_ids)
        profile.categories = categories
        
        self.profile_repo.create(profile)
        self.account_repo.update()
        return account

    def update_profile(self, account_id: int, target_account_id: int, is_admin: bool, data: ProfileUpdate) -> Account:
        target_account = self.get_account(target_account_id)

        # Admin cannot edit another admin
        if is_admin and target_account.role == Role.ADMIN and target_account.id != account_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit another admin")

        if data.password:
            target_account.hashed_password = self.auth_service.get_password_hash(data.password)

        if target_account.profile:
            if data.first_name is not None:
                target_account.profile.first_name = data.first_name
            if data.last_name is not None:
                target_account.profile.last_name = data.last_name
            if data.can_upload is not None and is_admin:
                target_account.profile.can_upload = data.can_upload
            if data.group_id is not None:
                target_account.profile.group_id = data.group_id
            if data.category_ids is not None:
                categories = self.profile_repo.get_categories_by_ids(data.category_ids)
                target_account.profile.categories = categories

        self.account_repo.update()
        return target_account

    def delete_account(self, account_id: int, target_account_id: int, is_admin: bool) -> None:
        target_account = self.get_account(target_account_id)
        
        if is_admin and target_account.role == Role.ADMIN and target_account.id != account_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete another admin")

        self.account_repo.delete(target_account)
