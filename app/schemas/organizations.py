from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.hotspots import Name


class OrganizationWrite(BaseModel):
    name: Name


class OrganizationRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class OrganizationRead(OrganizationRef):
    created_at: datetime
    member_count: int
