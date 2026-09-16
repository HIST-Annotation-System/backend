from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import CurrentUserDep, RequireRole
from app.dependencies.dictionaries import DictionaryRepositoryDep
from app.dependencies.resources import SessionDep
from app.models.account import Role
from app.schemas.category import CategoryCreate, CategoryResponse

router = APIRouter()

RequireAdmin = Annotated[CurrentUserDep, Depends(RequireRole(Role.ADMIN))]


@router.get("/", response_model=list[CategoryResponse])
def get_categories(repo: DictionaryRepositoryDep) -> list[CategoryResponse]:
    return repo.get_all_categories()


@router.post("/", response_model=CategoryResponse)
def create_category(
    request: CategoryCreate, 
    admin: RequireAdmin, 
    repo: DictionaryRepositoryDep,
    session: SessionDep
) -> CategoryResponse:
    category = repo.create_category(request.name)
    session.commit()
    return category


@router.delete("/{category_id}")
def delete_category(
    category_id: int, 
    admin: RequireAdmin, 
    repo: DictionaryRepositoryDep,
    session: SessionDep
) -> dict[str, str]:
    category = repo.get_category_by_id(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    repo.delete_category(category)
    session.commit()
    return {"status": "ok"}
