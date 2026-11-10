from pydantic import BaseModel, ConfigDict

from app.models.account import Role
from app.schemas.category import CategoryResponse
from app.schemas.organizations import OrganizationRef

class AccountCreate(BaseModel):
    username: str
    password: str
    role: Role = Role.ANNOTATOR
    can_upload: bool = False
    # Only the system admin chooses; an organization's admin creates accounts in their own organization.
    organization_id: int | None = None


class ProfileSetup(BaseModel):
    new_password: str
    first_name: str
    last_name: str
    category_ids: list[int] = []


class ProfileUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    can_upload: bool | None = None
    category_ids: list[int] | None = None
    password: str | None = None  # To allow changing password on profile update
    # Administrators only: role, and (system admin only) the organization.
    role: Role | None = None
    organization_id: int | None = None


class AnnotatorProfileResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    can_upload: bool
    categories: list[CategoryResponse] = []

    model_config = ConfigDict(from_attributes=True)


class AccountResponse(BaseModel):
    id: int
    username: str
    role: Role
    is_first_login: bool
    organization: OrganizationRef | None = None
    profile: AnnotatorProfileResponse | None = None

    model_config = ConfigDict(from_attributes=True)
