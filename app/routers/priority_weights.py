from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies.auth import CurrentUserDep, RequireRole
from app.dependencies.services import PriorityWeightServiceDep
from app.models.account import Role
from app.schemas.priority import PriorityWeights, PriorityWeightsUpdate

router = APIRouter()

RequireAdmin = Annotated[CurrentUserDep, Depends(RequireRole(Role.ADMIN))]


@router.get("/", response_model=PriorityWeights)
def get_priority_weights(service: PriorityWeightServiceDep, _user: CurrentUserDep) -> PriorityWeights:
    return PriorityWeights(weights=service.get())


@router.patch("/", response_model=PriorityWeights)
def update_priority_weights(
    data: PriorityWeightsUpdate, service: PriorityWeightServiceDep, _admin: RequireAdmin
) -> PriorityWeights:
    """Sets the listed components' weights; 0 switches a component off. The same for every organization."""
    return PriorityWeights(weights=service.update(data.weights))
