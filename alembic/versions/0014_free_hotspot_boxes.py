"""hotspot batches list each box's level-0 corner (grid or freely painted), not grid cells

Revision ID: 0014_free_hotspot_boxes
Revises: 0013_hotspot_batches
Create Date: 2026-10-14 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0014_free_hotspot_boxes'
down_revision: Union[str, Sequence[str], None] = '0013_hotspot_batches'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (batches from before list grid cells, so they are dropped: they are only progress records)."""
    op.execute("DELETE FROM hotspot_batches")
    op.drop_column('hotspot_batches', 'x')
    op.drop_column('hotspot_batches', 'y')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM hotspot_batches")
    op.add_column('hotspot_batches', sa.Column('x', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('hotspot_batches', sa.Column('y', sa.Integer(), nullable=False, server_default='0'))
