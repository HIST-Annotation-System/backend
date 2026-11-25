from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.core.constants import PASSWORD_MIN_LENGTH
from app.schemas.user import AccountCreate, ProfileSetup, ProfileUpdate

SHORT = "a" * (PASSWORD_MIN_LENGTH - 1)
LONG_ENOUGH = "a" * PASSWORD_MIN_LENGTH

CASES: list[tuple[type[BaseModel], str, dict[str, Any]]] = [
    (AccountCreate, "password", {"username": "anna"}),
    (ProfileSetup, "new_password", {"first_name": "Anna", "last_name": "Nowak"}),
    (ProfileUpdate, "password", {}),
]


@pytest.mark.parametrize(("schema", "field", "rest"), CASES)
def test_a_too_short_password_is_refused(schema: type[BaseModel], field: str, rest: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        schema(**rest, **{field: SHORT})


@pytest.mark.parametrize(("schema", "field", "rest"), CASES)
def test_a_password_of_the_minimum_length_is_accepted(
    schema: type[BaseModel], field: str, rest: dict[str, Any]
) -> None:
    assert getattr(schema(**rest, **{field: LONG_ENOUGH}), field) == LONG_ENOUGH


def test_a_profile_update_without_a_password_keeps_it() -> None:
    assert ProfileUpdate(first_name="Anna").password is None
