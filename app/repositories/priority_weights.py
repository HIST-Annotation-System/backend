from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.priority_weight import PriorityComponent, PriorityWeight


class PriorityWeightRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def as_dict(self) -> dict[PriorityComponent, float]:
        return {row.component: row.weight for row in self._session.scalars(select(PriorityWeight))}

    def set_weights(self, weights: dict[PriorityComponent, float]) -> None:
        for row in self._session.scalars(select(PriorityWeight)):
            if row.component in weights:
                row.weight = weights[row.component]
        self._session.flush()
