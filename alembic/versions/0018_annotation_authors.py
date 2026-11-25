"""annotation authors: who drew each hotspot annotation and who marked a hotspot reannotated

Revision ID: 0018_annotation_authors
Revises: 0017_cell_type_name_ci_unique
Create Date: 2026-10-24 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0018_annotation_authors'
down_revision: Union[str, Sequence[str], None] = '0017_cell_type_name_ci_unique'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (purely additive: existing annotations and marks stay unattributed)."""
    op.add_column('hotspot_annotations', sa.Column('created_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_hotspot_annotations_created_by_id_accounts'),
        'hotspot_annotations', 'accounts', ['created_by_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(
        op.f('ix_hotspot_annotations_created_by_id'), 'hotspot_annotations', ['created_by_id'], unique=False
    )
    op.add_column('hotspots', sa.Column('reannotated_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_hotspots_reannotated_by_id_accounts'),
        'hotspots', 'accounts', ['reannotated_by_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(op.f('ix_hotspots_reannotated_by_id'), 'hotspots', ['reannotated_by_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_hotspots_reannotated_by_id'), table_name='hotspots')
    op.drop_constraint(op.f('fk_hotspots_reannotated_by_id_accounts'), 'hotspots', type_='foreignkey')
    op.drop_column('hotspots', 'reannotated_by_id')
    op.drop_index(op.f('ix_hotspot_annotations_created_by_id'), table_name='hotspot_annotations')
    op.drop_constraint(
        op.f('fk_hotspot_annotations_created_by_id_accounts'), 'hotspot_annotations', type_='foreignkey'
    )
    op.drop_column('hotspot_annotations', 'created_by_id')
