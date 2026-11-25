from typing import Annotated

from fastapi import Depends

from app.dependencies.resources import SessionDep
from app.repositories.dictionaries import CategoryGroupRepository

def get_category_group_repository(session: SessionDep) -> CategoryGroupRepository:
    return CategoryGroupRepository(session)

CategoryGroupRepositoryDep = Annotated[CategoryGroupRepository, Depends(get_category_group_repository)]
