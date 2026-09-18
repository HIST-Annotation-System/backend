from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import CurrentUserDep, RequireRole
from app.dependencies.dictionaries import CategoryGroupRepositoryDep
from app.dependencies.resources import SessionDep
from app.models.account import Role
from app.schemas.group import GroupCreate, AnnotationGroupResponse

router = APIRouter()

RequireAdmin = Annotated[CurrentUserDep, Depends(RequireRole(Role.ADMIN))]


@router.get("/", response_model=list[AnnotationGroupResponse])
def get_groups(repo: CategoryGroupRepositoryDep) -> list[AnnotationGroupResponse]:
    return repo.get_all_groups()


@router.post("/", response_model=AnnotationGroupResponse)
def create_group(
    request: GroupCreate,
    _admin: RequireAdmin,
    repo: CategoryGroupRepositoryDep,
    session: SessionDep
) -> AnnotationGroupResponse:
    group = repo.create_group(request.name)
    session.commit()
    return group


@router.delete("/{group_id}")
def delete_group(
    group_id: int,
    _admin: RequireAdmin,
    repo: CategoryGroupRepositoryDep,
    session: SessionDep
) -> dict[str, str]:
    group = repo.get_group_by_id(group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    repo.delete_group(group)
    session.commit()
    return {"status": "ok"}
