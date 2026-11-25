"""seed annotation classes

Seeds the tissue classes used by the prototype annotator (previously hard-coded in the UI).

Revision ID: 0002_seed_annotation_classes
Revises: 0001_initial_schema
Create Date: 2026-09-11 17:10:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0002_seed_annotation_classes'
down_revision: Union[str, Sequence[str], None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Self-contained snapshot of the table (migrations must not import application models).
annotation_classes = sa.table(
    'annotation_classes',
    sa.column('id', sa.Uuid()),
    sa.column('name', sa.String()),
    sa.column('color', sa.String()),
    sa.column('sort_order', sa.Integer()),
)

SEED_CLASSES = [
    {'id': uuid.UUID('938c0669-fefc-4efa-90c7-1dde449bab73'), 'name': 'Tkanka nowotworowa', 'color': '#ef4444', 'sort_order': 1},
    {'id': uuid.UUID('67d20d2f-7fe3-40f6-a520-c045e5d509e1'), 'name': 'Tkanka zdrowa', 'color': '#22c55e', 'sort_order': 2},
    {'id': uuid.UUID('58b75e80-dd1b-4ee3-b5c7-a87ba83c7048'), 'name': 'Zrąb tkankowy', 'color': '#eab308', 'sort_order': 3},
    {'id': uuid.UUID('4ba7e2e9-a9dd-4bf1-8488-930044bfe1f4'), 'name': 'Inna struktura', 'color': '#a855f7', 'sort_order': 4},
]


def upgrade() -> None:
    """Insert the default annotation classes."""
    op.bulk_insert(annotation_classes, SEED_CLASSES)


def downgrade() -> None:
    """Remove the default annotation classes."""
    op.execute(annotation_classes.delete().where(annotation_classes.c.id.in_([item['id'] for item in SEED_CLASSES])))
