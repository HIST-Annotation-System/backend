"""hotspots: folders, square slide snapshots and their annotations

Revision ID: 0007_hotspots
Revises: 0006_rename_cancer_class
Create Date: 2026-10-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0007_hotspots'
down_revision: Union[str, Sequence[str], None] = '0006_rename_cancer_class'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('hotspot_folders',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hotspot_folders')),
    sa.UniqueConstraint('name', name=op.f('uq_hotspot_folders_name'))
    )
    op.create_table('hotspots',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('folder_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('slide_id', sa.Uuid(), nullable=True),
    sa.Column('slide_name', sa.String(length=255), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('image_size', sa.Integer(), nullable=False),
    sa.Column('mpp', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['folder_id'], ['hotspot_folders.id'], name=op.f('fk_hotspots_folder_id_hotspot_folders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['slide_id'], ['slides.id'], name=op.f('fk_hotspots_slide_id_slides'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hotspots'))
    )
    op.create_index(op.f('ix_hotspots_folder_id'), 'hotspots', ['folder_id'], unique=False)
    op.create_index(op.f('ix_hotspots_slide_id'), 'hotspots', ['slide_id'], unique=False)
    op.create_table('hotspot_annotations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('hotspot_id', sa.Uuid(), nullable=False),
    sa.Column('class_id', sa.Uuid(), nullable=False),
    sa.Column('geometry', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['class_id'], ['annotation_classes.id'], name=op.f('fk_hotspot_annotations_class_id_annotation_classes')),
    sa.ForeignKeyConstraint(['hotspot_id'], ['hotspots.id'], name=op.f('fk_hotspot_annotations_hotspot_id_hotspots'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hotspot_annotations'))
    )
    op.create_index(op.f('ix_hotspot_annotations_hotspot_id'), 'hotspot_annotations', ['hotspot_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_hotspot_annotations_hotspot_id'), table_name='hotspot_annotations')
    op.drop_table('hotspot_annotations')
    op.drop_index(op.f('ix_hotspots_slide_id'), table_name='hotspots')
    op.drop_index(op.f('ix_hotspots_folder_id'), table_name='hotspots')
    op.drop_table('hotspots')
    op.drop_table('hotspot_folders')
