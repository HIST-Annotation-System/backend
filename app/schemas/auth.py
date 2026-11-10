from pydantic import BaseModel

from app.models.account import Role
from app.schemas.organizations import OrganizationRef


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    # Additional state metadata can be useful for frontend routing
    is_first_login: bool = False
    role: Role | None = None
    organization: OrganizationRef | None = None
