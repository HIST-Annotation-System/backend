import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, Row, and_, case, distinct, func, null, select, union_all
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.cell_type import CellType
from app.models.hotspot import AnnotationKind, AnnotationSource, Hotspot, HotspotAnnotation, HotspotFolder
from app.models.profile import AnnotatorProfile
from app.models.slide import Slide, SlideStatus
from app.models.tag import HotspotRating, HotspotTag, Tag, TagDimension
from app.schemas.hotspots import HotspotStatus


class StatsRepository:
    """Read-only aggregates over the whole catalog, for the admin statistics."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def hotspot_status_by_folder(self) -> Sequence[Row[tuple[uuid.UUID, str, str | None, int]]]:
        """(folder id, folder name, HotspotStatus value, hotspot count); an empty folder has one row with status
        None and count 0."""
        # Tagged once the ratings (of anyone) set both dimensions, as in app.services.hotspot_ratings.
        verdicts = (
            select(
                HotspotRating.hotspot_id,
                func.count(HotspotRating.importance_tag_id).label("importance"),
                func.count(HotspotRating.accuracy_tag_id).label("accuracy"),
            )
            .group_by(HotspotRating.hotspot_id)
            .subquery()
        )
        status = case(
            (Hotspot.id.is_(None), null()),
            (Hotspot.reannotated_at.is_not(None), HotspotStatus.REANNOTATED.value),
            (
                and_(func.coalesce(verdicts.c.importance, 0) > 0, func.coalesce(verdicts.c.accuracy, 0) > 0),
                HotspotStatus.TO_REANNOTATE.value,
            ),
            else_=HotspotStatus.UNTAGGED.value,
        )
        # Status computed in a subquery first: PostgreSQL would not match the CASE in GROUP BY to the one in the
        # select list, since each gets its own bound parameters.
        per_hotspot = (
            select(
                HotspotFolder.id.label("folder_id"),
                HotspotFolder.name.label("folder_name"),
                Hotspot.id.label("hotspot_id"),
                status.label("status"),
            )
            .outerjoin(Hotspot, Hotspot.folder_id == HotspotFolder.id)
            .outerjoin(verdicts, verdicts.c.hotspot_id == Hotspot.id)
            .subquery()
        )
        statement = (
            select(
                per_hotspot.c.folder_id,
                per_hotspot.c.folder_name,
                per_hotspot.c.status,
                func.count(per_hotspot.c.hotspot_id),
            )
            .group_by(per_hotspot.c.folder_id, per_hotspot.c.folder_name, per_hotspot.c.status)
            .order_by(per_hotspot.c.folder_name)
        )
        return self._session.execute(statement).all()

    def count_annotations(self, unattributed_only: bool = False) -> int:
        """Cell annotations by people (excluded areas and untouched model cells are not work counted here)."""
        statement = select(func.count(HotspotAnnotation.id)).where(
            HotspotAnnotation.kind == AnnotationKind.CELL, HotspotAnnotation.source == AnnotationSource.HUMAN
        )
        if unattributed_only:
            statement = statement.where(HotspotAnnotation.created_by_id.is_(None))
        return self._session.scalar(statement) or 0

    def reannotation_times(self) -> Sequence[Row[tuple[datetime, datetime | None]]]:
        """(created_at, reannotated_at) of every hotspot marked reannotated."""
        return self._session.execute(
            select(Hotspot.created_at, Hotspot.reannotated_at).where(Hotspot.reannotated_at.is_not(None))
        ).all()

    def slides_by_status(self) -> Sequence[Row[tuple[SlideStatus, int, int | None]]]:
        """(status, slide count, summed file size)."""
        return self._session.execute(
            select(Slide.status, func.count(Slide.id), func.sum(Slide.file_size_bytes)).group_by(Slide.status)
        ).all()

    def accounts(self) -> Sequence[Row[Any]]:
        """(id, username, role, is_first_login, first_name, last_name) of every account; the names are None for an
        account without a profile."""
        return self._session.execute(
            select(
                Account.id,
                Account.username,
                Account.role,
                Account.is_first_login,
                AnnotatorProfile.first_name,
                AnnotatorProfile.last_name,
            )
            .outerjoin(AnnotatorProfile, AnnotatorProfile.account_id == Account.id)
            .order_by(Account.username)
        ).all()

    def in_progress_by_account(self) -> dict[int, int]:
        """Account id -> hotspots not yet marked reannotated that hold an annotation of theirs."""
        return self._count_by(
            select(HotspotAnnotation.created_by_id, func.count(distinct(HotspotAnnotation.hotspot_id)))
            .join(Hotspot, Hotspot.id == HotspotAnnotation.hotspot_id)
            .where(HotspotAnnotation.created_by_id.is_not(None), Hotspot.reannotated_at.is_(None))
            .group_by(HotspotAnnotation.created_by_id)
        )

    def finished_by_account(self) -> dict[int, int]:
        return self._count_by(
            select(Hotspot.reannotated_by_id, func.count(Hotspot.id))
            .where(Hotspot.reannotated_by_id.is_not(None), Hotspot.reannotated_at.is_not(None))
            .group_by(Hotspot.reannotated_by_id)
        )

    def verdicts_by_account(self) -> dict[int, int]:
        """Account id -> tags it put on hotspots: the dimensions set in its ratings, and custom tags."""
        uses = self._tag_uses().subquery()
        return self._count_by(
            select(uses.c.account_id, func.count()).where(uses.c.account_id.is_not(None)).group_by(uses.c.account_id)
        )

    def annotations_by_account(self, recent_since: datetime) -> Sequence[Row[tuple[int | None, int, int, datetime]]]:
        """(account id, cell annotations, cell annotations since `recent_since`, latest cell annotation time)."""
        return self._session.execute(
            select(
                HotspotAnnotation.created_by_id,
                func.count(HotspotAnnotation.id),
                func.count(HotspotAnnotation.id).filter(HotspotAnnotation.created_at >= recent_since),
                func.max(HotspotAnnotation.created_at),
            )
            .where(HotspotAnnotation.created_by_id.is_not(None), HotspotAnnotation.kind == AnnotationKind.CELL)
            .group_by(HotspotAnnotation.created_by_id)
        ).all()

    def last_reannotated_by_account(self) -> dict[int, datetime]:
        rows = self._session.execute(
            select(Hotspot.reannotated_by_id, func.max(Hotspot.reannotated_at))
            .where(Hotspot.reannotated_by_id.is_not(None))
            .group_by(Hotspot.reannotated_by_id)
        ).all()
        return {account_id: latest for account_id, latest in rows}

    def cell_type_usage(self) -> Sequence[Row[tuple[int, str, int]]]:
        """(id, name, annotation count by people) of every cell type, most used first."""
        count = func.count(HotspotAnnotation.id)
        return self._session.execute(
            select(CellType.id, CellType.name, count)
            .outerjoin(
                HotspotAnnotation,
                and_(
                    HotspotAnnotation.cell_type_id == CellType.id,
                    HotspotAnnotation.source == AnnotationSource.HUMAN,
                ),
            )
            .group_by(CellType.id, CellType.name)
            .order_by(count.desc(), CellType.name)
        ).all()

    def tag_usage(self) -> Sequence[Row[tuple[int, str, TagDimension | None, int]]]:
        """(id, name, dimension, tagged hotspot count) of every tag."""
        uses = self._tag_uses().subquery()
        return self._session.execute(
            select(Tag.id, Tag.name, Tag.dimension, func.count(distinct(uses.c.hotspot_id)))
            .outerjoin(uses, uses.c.tag_id == Tag.id)
            .group_by(Tag.id, Tag.name, Tag.dimension)
            .order_by(Tag.dimension.is_(None), Tag.dimension.desc(), Tag.weight.desc(), Tag.name)
        ).all()

    def daily_counts(self, column: Any, since: datetime, *where: ColumnElement[bool]) -> dict[date, int]:
        """Day (in the database time zone) -> rows whose `column` timestamp falls on it, from `since` on, among
        those matching `where`."""
        day: ColumnElement[date] = func.date(column)
        rows = self._session.execute(
            select(day, func.count()).where(column >= since, *where).group_by(day)
        ).all()
        return {_as_date(value): count for value, count in rows}

    @staticmethod
    def _tag_uses() -> Any:
        """(hotspot id, tag id, account id) of every custom tag on a hotspot and every dimension of every rating."""
        return union_all(
            select(HotspotTag.hotspot_id, HotspotTag.tag_id, HotspotTag.added_by_id.label("account_id")),
            *(
                select(HotspotRating.hotspot_id, column.label("tag_id"), HotspotRating.account_id)
                .where(column.is_not(None))
                for column in (HotspotRating.importance_tag_id, HotspotRating.accuracy_tag_id)
            ),
        )

    def _count_by(self, statement: Any) -> dict[int, int]:
        return {key: count for key, count in self._session.execute(statement).all()}


def _as_date(value: date | str) -> date:
    # PostgreSQL returns a date; SQLite (tests) returns its ISO text.
    return value if isinstance(value, date) else date.fromisoformat(value)
