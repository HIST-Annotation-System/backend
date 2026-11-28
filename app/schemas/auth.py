from pydantic import BaseModel

from app.models.account import Role
from app.schemas.organizations import OrganizationRef


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    # For API clients that send it as a Bearer token (scripts, the perf tests). The browser uses the HttpOnly cookies,
    # and the refresh token is only ever in its cookie.
    access_token: str | None = None
    token_type: str = "bearer"
    # Additional state metadata can be useful for frontend routing
    is_first_login: bool = False
    role: Role | None = None
    organization: OrganizationRef | None = None
