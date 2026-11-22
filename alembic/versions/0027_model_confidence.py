"""the model's confidence in its cells, averaged per hotspot, and rescoring it on request

Hotspots segmented before this have none until someone rescores their folder (see app.services.segmentation).

Revision ID: 0027_model_confidence
Revises: 0026_priority_weights
Create Date: 2026-11-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0027_model_confidence'
down_revision: Union[str, Sequence[str], None] = '0026_priority_weights'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('hotspots', sa.Column('model_outline_confidence', sa.Float(), nullable=True))
    op.add_column('hotspots', sa.Column('model_class_confidence', sa.Float(), nullable=True))
    op.add_column(
        'hotspots', sa.Column('confidence_pending', sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column('hotspots', sa.Column('confidence_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('hotspots', sa.Column('confidence_error', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hotspots', 'confidence_error')
    op.drop_column('hotspots', 'confidence_requested_at')
    op.drop_column('hotspots', 'confidence_pending')
    op.drop_column('hotspots', 'model_class_confidence')
    op.drop_column('hotspots', 'model_outline_confidence')
