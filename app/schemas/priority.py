from typing import Annotated, Self

from pydantic import BaseModel, Field, model_validator

from app.models.priority_weight import PriorityComponent

PRIORITY_WEIGHT_MAX = 100
PRIORITY_WEIGHTS_EMPTY = "Provide at least one component weight."

Weight = Annotated[float, Field(ge=0, le=PRIORITY_WEIGHT_MAX)]


class PriorityWeights(BaseModel):
    """Component -> weight; an update may list only the components it changes."""

    weights: dict[PriorityComponent, Weight]


class PriorityWeightsUpdate(PriorityWeights):
    @model_validator(mode="after")
    def _require_weights(self) -> Self:
        if not self.weights:
            raise ValueError(PRIORITY_WEIGHTS_EMPTY)
        return self
