"""hotspot tags: shared expert tags (core importance/accuracy verdicts seeded) and the reannotated mark

Revision ID: 0016_hotspot_tags
Revises: 0015_hotspot_annotation_curve
Create Date: 2026-10-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0016_hotspot_tags'
down_revision: Union[str, Sequence[str], None] = '0015_hotspot_annotation_curve'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE_TAGS = [
    {'name': 'wysoka', 'dimension': 'importance', 'weight': 100},
    {'name': 'średnia', 'dimension': 'importance', 'weight': 50},
    {'name': 'niska', 'dimension': 'importance', 'weight': 10},
    {'name': 'błędna', 'dimension': 'accuracy', 'weight': 100},
    {'name': 'poprawna', 'dimension': 'accuracy', 'weight': 0},
]


def upgrade() -> None:
    """Upgrade schema (purely additive: existing hotspots start untagged)."""
    tags = op.create_table('tags',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('dimension', sa.Enum('importance', 'accuracy', name='tag_dimension', native_enum=False, create_constraint=True, length=16), nullable=True),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['accounts.id'], name=op.f('fk_tags_created_by_id_accounts'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tags')),
    sa.UniqueConstraint('name', name=op.f('uq_tags_name'))
    )
    op.create_table('hotspot_tags',
    sa.Column('hotspot_id', sa.Uuid(), nullable=False),
    sa.Column('tag_id', sa.Integer(), nullable=False),
    sa.Column('added_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['added_by_id'], ['accounts.id'], name=op.f('fk_hotspot_tags_added_by_id_accounts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['hotspot_id'], ['hotspots.id'], name=op.f('fk_hotspot_tags_hotspot_id_hotspots'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], name=op.f('fk_hotspot_tags_tag_id_tags'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('hotspot_id', 'tag_id', name=op.f('pk_hotspot_tags'))
    )
    op.create_index(op.f('ix_hotspot_tags_tag_id'), 'hotspot_tags', ['tag_id'], unique=False)
    op.add_column('hotspots', sa.Column('reannotated_at', sa.DateTime(timezone=True), nullable=True))
    op.bulk_insert(tags, CORE_TAGS)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hotspots', 'reannotated_at')
    op.drop_index(op.f('ix_hotspot_tags_tag_id'), table_name='hotspot_tags')
    op.drop_table('hotspot_tags')
    op.drop_table('tags')
