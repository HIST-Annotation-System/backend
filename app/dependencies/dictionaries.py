from typing import Annotated

from fastapi import Depends

from app.dependencies.resources import SessionDep
from app.repositories.dictionaries import DictionaryRepository

def get_dictionary_repository(session: SessionDep) -> DictionaryRepository:
    return DictionaryRepository(session)

DictionaryRepositoryDep = Annotated[DictionaryRepository, Depends(get_dictionary_repository)]
