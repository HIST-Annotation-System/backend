"""hotspot annotation curve: the closed Bezier path a polygon was drawn as

Revision ID: 0015_hotspot_annotation_curve
Revises: 0014_free_hotspot_boxes
Create Date: 2026-10-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0015_hotspot_annotation_curve'
down_revision: Union[str, Sequence[str], None] = '0014_free_hotspot_boxes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (purely additive: existing annotations stay plain polygons with a NULL curve)."""
    op.add_column(
        'hotspot_annotations',
        sa.Column('curve', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hotspot_annotations', 'curve')
