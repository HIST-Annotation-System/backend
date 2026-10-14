"""hotspot batches (grids of slide squares cut out in the background); drop slide-level annotations

Revision ID: 0013_hotspot_batches
Revises: 0012_simplify_cell_types
Create Date: 2026-10-14 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0013_hotspot_batches'
down_revision: Union[str, Sequence[str], None] = '0012_simplify_cell_types'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('hotspot_batches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('slide_id', sa.Uuid(), nullable=False),
    sa.Column('slide_name', sa.String(length=255), nullable=False),
    sa.Column('folder_id', sa.Uuid(), nullable=False),
    sa.Column('name_prefix', sa.String(length=255), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('boxes', JSON, nullable=False),
    sa.Column('done', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('running', 'done', 'failed', name='hotspot_batch_status', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['folder_id'], ['hotspot_folders.id'], name=op.f('fk_hotspot_batches_folder_id_hotspot_folders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['slide_id'], ['slides.id'], name=op.f('fk_hotspot_batches_slide_id_slides'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hotspot_batches'))
    )
    op.create_index(op.f('ix_hotspot_batches_folder_id'), 'hotspot_batches', ['folder_id'], unique=False)
    op.create_index(op.f('ix_hotspot_batches_slide_id'), 'hotspot_batches', ['slide_id'], unique=False)
    op.create_index(op.f('ix_hotspot_batches_status'), 'hotspot_batches', ['status'], unique=False)

    # The slide viewer only cuts out hotspots now; annotating happens on hotspots.
    op.drop_table('annotations')


def downgrade() -> None:
    """Downgrade schema (the dropped slide-level annotations come back empty)."""
    op.create_table('annotations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('slide_id', sa.Uuid(), nullable=False),
    sa.Column('cell_type_id', sa.Integer(), nullable=True),
    sa.Column('geometry', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['slide_id'], ['slides.id'], name=op.f('fk_annotations_slide_id_slides'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['cell_type_id'], ['cell_types.id'], name=op.f('fk_annotations_cell_type_id_cell_types'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_annotations'))
    )
    op.create_index(op.f('ix_annotations_slide_id'), 'annotations', ['slide_id'], unique=False)
    op.create_index(op.f('ix_annotations_cell_type_id'), 'annotations', ['cell_type_id'], unique=False)

    op.drop_index(op.f('ix_hotspot_batches_status'), table_name='hotspot_batches')
    op.drop_index(op.f('ix_hotspot_batches_slide_id'), table_name='hotspot_batches')
    op.drop_index(op.f('ix_hotspot_batches_folder_id'), table_name='hotspot_batches')
    op.drop_table('hotspot_batches')
