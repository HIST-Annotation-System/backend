from pydantic import BaseModel, ConfigDict

from app.models.account import Role
from app.schemas.category import CategoryResponse
from app.schemas.group import AnnotationGroupResponse

class AccountCreate(BaseModel):
    username: str
    password: str
    role: Role = Role.ANNOTATOR
    can_upload: bool = False
    group_id: int | None = None


class ProfileSetup(BaseModel):
    new_password: str
    first_name: str
    last_name: str
    group_id: int | None = None
    category_ids: list[int] = []


class ProfileUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    can_upload: bool | None = None
    group_id: int | None = None
    category_ids: list[int] | None = None
    password: str | None = None  # To allow changing password on profile update


class AnnotatorProfileResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    can_upload: bool
    group: AnnotationGroupResponse | None = None
    categories: list[CategoryResponse] = []

    model_config = ConfigDict(from_attributes=True)


class AccountResponse(BaseModel):
    id: int
    username: str
    role: Role
    is_first_login: bool
    profile: AnnotatorProfileResponse | None = None

    model_config = ConfigDict(from_attributes=True)
