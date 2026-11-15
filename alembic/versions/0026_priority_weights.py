"""admin-set weights of the estimated priority of unrated hotspots

Stored `features` JSON needs no migration: hotspots missing the new descriptors get them on the next listing, and
the unused `colorfulness` value stays in old rows.

Revision ID: 0026_priority_weights
Revises: 0025_annotation_confirmed
Create Date: 2026-11-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0026_priority_weights'
down_revision: Union[str, Sequence[str], None] = '0025_annotation_confirmed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COMPONENTS = ('cell_density', 'hematoxylin', 'glcm_contrast', 'edge_density', 'entropy')
DEFAULT_WEIGHTS = {
    'cell_density': 0.35,
    'hematoxylin': 0.20,
    'glcm_contrast': 0.15,
    'edge_density': 0.15,
    'entropy': 0.15,
}


def upgrade() -> None:
    """Upgrade schema."""
    table = op.create_table(
        'priority_weights',
        sa.Column(
            'component',
            sa.Enum(*COMPONENTS, name='priority_component', native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column('weight', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('component'),
    )
    op.bulk_insert(table, [{'component': name, 'weight': weight} for name, weight in DEFAULT_WEIGHTS.items()])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('priority_weights')
