"""annotation kind: a hotspot annotation is a cell or an area excluded from model training

Revision ID: 0019_annotation_kind
Revises: 0018_annotation_authors
Create Date: 2026-10-26 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0019_annotation_kind'
down_revision: Union[str, Sequence[str], None] = '0018_annotation_authors'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (purely additive: existing annotations are cells)."""
    # Batch mode, so SQLite (which cannot add a constraint to an existing table) gets the CHECK too.
    with op.batch_alter_table('hotspot_annotations') as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=16), server_default='cell', nullable=False))
        batch_op.create_check_constraint(
            op.f('ck_hotspot_annotations_annotation_kind'), "kind IN ('cell', 'excluded')"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('hotspot_annotations') as batch_op:
        batch_op.drop_constraint(op.f('ck_hotspot_annotations_annotation_kind'), type_='check')
        batch_op.drop_column('kind')
