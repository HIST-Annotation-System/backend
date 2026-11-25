"""hotspot features: image descriptors for sorting the catalog

Revision ID: 0009_hotspot_features
Revises: 0008_uploaded_hotspots
Create Date: 2026-10-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0009_hotspot_features'
down_revision: Union[str, Sequence[str], None] = '0008_uploaded_hotspots'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (existing hotspots get their features computed on first listing)."""
    op.add_column(
        'hotspots',
        sa.Column('features', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hotspots', 'features')
