"""two tissue classes

Annotation uses two classes: "Tkanka nowotworowa" and "Zrąb tkankowy"; unannotated tissue is negative.
Annotations of the dropped "Tkanka zdrowa" (negative by definition) and "Inna struktura" (too heterogeneous
to fold into stroma) are deleted. The downgrade restores the classes, not those annotations.

Revision ID: 0005_two_tissue_classes
Revises: 0004_strict_slide_formats
Create Date: 2026-09-17 15:05:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0005_two_tissue_classes'
down_revision: Union[str, Sequence[str], None] = '0004_strict_slide_formats'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Self-contained snapshots of the tables (migrations must not import application models).
annotation_classes = sa.table(
    'annotation_classes',
    sa.column('id', sa.Uuid()),
    sa.column('name', sa.String()),
    sa.column('color', sa.String()),
    sa.column('sort_order', sa.Integer()),
)
annotations = sa.table('annotations', sa.column('class_id', sa.Uuid()))

STROMA_ID = uuid.UUID('58b75e80-dd1b-4ee3-b5c7-a87ba83c7048')
DROPPED_CLASSES = [
    {'id': uuid.UUID('67d20d2f-7fe3-40f6-a520-c045e5d509e1'), 'name': 'Tkanka zdrowa', 'color': '#22c55e', 'sort_order': 2},
    {'id': uuid.UUID('4ba7e2e9-a9dd-4bf1-8488-930044bfe1f4'), 'name': 'Inna struktura', 'color': '#a855f7', 'sort_order': 4},
]
DROPPED_IDS = [item['id'] for item in DROPPED_CLASSES]


def upgrade() -> None:
    """Drop the healthy-tissue and other-structure classes with their annotations."""
    op.execute(annotations.delete().where(annotations.c.class_id.in_(DROPPED_IDS)))
    op.execute(annotation_classes.delete().where(annotation_classes.c.id.in_(DROPPED_IDS)))
    op.execute(annotation_classes.update().where(annotation_classes.c.id == STROMA_ID).values(sort_order=2))


def downgrade() -> None:
    """Restore the dropped classes (their deleted annotations are not restored)."""
    op.execute(annotation_classes.update().where(annotation_classes.c.id == STROMA_ID).values(sort_order=3))
    op.bulk_insert(annotation_classes, DROPPED_CLASSES)
