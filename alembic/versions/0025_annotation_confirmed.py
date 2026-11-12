"""annotators confirm copies of model cells they leave unchanged

A copy of a model cell that its author looked at and kept as it is gets `confirmed`, so it counts as dealt with like
the cells they changed.

Revision ID: 0025_annotation_confirmed
Revises: 0024_shared_hotspot_verdict
Create Date: 2026-11-12 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0025_annotation_confirmed'
down_revision: Union[str, Sequence[str], None] = '0024_shared_hotspot_verdict'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'hotspot_annotations',
        sa.Column('confirmed', sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hotspot_annotations', 'confirmed')
