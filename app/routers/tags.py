from fastapi import APIRouter, status

from app.dependencies.auth import CurrentUserDep, ManagerDep
from app.dependencies.services import TagServiceDep
from app.schemas.tags import TagCreate, TagRead, TagUpdate

router = APIRouter()


@router.get("/", response_model=list[TagRead])
def list_tags(service: TagServiceDep, _user: CurrentUserDep) -> list[TagRead]:
    """The global tags and the user's organization's (every organization's for the system admin)."""
    return service.list_tags()


@router.post("/", response_model=TagRead, status_code=status.HTTP_201_CREATED)
def create_tag(data: TagCreate, service: TagServiceDep, user: CurrentUserDep) -> TagRead:
    """Anyone can add a tag: global when the system admin does, otherwise their organization's. It must weigh less
    than the lightest non-zero core tag."""
    return service.create_tag(data, user)


@router.patch("/{tag_id}", response_model=TagRead)
def update_tag(tag_id: int, data: TagUpdate, service: TagServiceDep, user: ManagerDep) -> TagRead:
    """Global tags by the system admin, an organization's by its admin. Core tags can only be re-weighted; 409 if
    custom tags would then outweigh them."""
    return service.update_tag(tag_id, data, user)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: int, service: TagServiceDep, user: ManagerDep) -> None:
    """Global tags by the system admin, an organization's by its admin."""
    service.delete_tag(tag_id, user)
