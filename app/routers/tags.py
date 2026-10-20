from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.dependencies.auth import CurrentUserDep, RequireRole
from app.dependencies.services import TagServiceDep
from app.models.account import Role
from app.schemas.tags import TagCreate, TagRead, TagUpdate

router = APIRouter()

RequireAdmin = Annotated[CurrentUserDep, Depends(RequireRole(Role.ADMIN))]


@router.get("/", response_model=list[TagRead])
def list_tags(service: TagServiceDep, _user: CurrentUserDep) -> list[TagRead]:
    return service.list_tags()


@router.post("/", response_model=TagRead, status_code=status.HTTP_201_CREATED)
def create_tag(data: TagCreate, service: TagServiceDep, user: CurrentUserDep) -> TagRead:
    """Any expert can add a tag; it must weigh less than the lightest non-zero core tag."""
    return service.create_tag(data, user)


@router.patch("/{tag_id}", response_model=TagRead)
def update_tag(tag_id: int, data: TagUpdate, service: TagServiceDep, _admin: RequireAdmin) -> TagRead:
    """Core tags can only be re-weighted; 409 if custom tags would then outweigh them."""
    return service.update_tag(tag_id, data)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: int, service: TagServiceDep, _admin: RequireAdmin) -> None:
    service.delete_tag(tag_id)
