"""one shared importance/accuracy verdict per hotspot again, instead of one rating per annotator

Each hotspot keeps the latest rating's importance and accuracy (with its author) as the shared verdicts on
hotspot_tags, and the hotspot_ratings table is dropped.

Revision ID: 0024_shared_hotspot_verdict
Revises: 0023_reannotation_agreement
Create Date: 2026-11-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0024_shared_hotspot_verdict'
down_revision: Union[str, Sequence[str], None] = '0023_reannotation_agreement'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE_TAG_IDS = "SELECT id FROM tags WHERE dimension IS NOT NULL"


def upgrade() -> None:
    """Upgrade schema."""
    for column in ('importance_tag_id', 'accuracy_tag_id'):
        op.execute(
            f"""
            INSERT INTO hotspot_tags (hotspot_id, tag_id, added_by_id, created_at)
            SELECT hotspot_id, {column}, account_id, updated_at
            FROM (
                SELECT hotspot_id, {column}, account_id, updated_at,
                       ROW_NUMBER() OVER (PARTITION BY hotspot_id ORDER BY updated_at DESC, id DESC) AS position
                FROM hotspot_ratings
                WHERE {column} IS NOT NULL
            ) AS latest
            WHERE position = 1
            """
        )
    op.drop_index(op.f('ix_hotspot_ratings_hotspot_id'), table_name='hotspot_ratings')
    op.drop_index(op.f('ix_hotspot_ratings_account_id'), table_name='hotspot_ratings')
    op.drop_table('hotspot_ratings')


def downgrade() -> None:
    """Downgrade schema: every shared verdict becomes a rating of its author (those without one are dropped)."""
    op.create_table(
        'hotspot_ratings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('hotspot_id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('importance_tag_id', sa.Integer(), nullable=True),
        sa.Column('accuracy_tag_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_hotspot_ratings_account_id_accounts'), ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['accuracy_tag_id'], ['tags.id'], name=op.f('fk_hotspot_ratings_accuracy_tag_id_tags'), ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['hotspot_id'], ['hotspots.id'], name=op.f('fk_hotspot_ratings_hotspot_id_hotspots'), ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['importance_tag_id'], ['tags.id'], name=op.f('fk_hotspot_ratings_importance_tag_id_tags'),
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_hotspot_ratings')),
        sa.UniqueConstraint('hotspot_id', 'account_id', name=op.f('uq_hotspot_ratings_hotspot_id')),
    )
    op.create_index(op.f('ix_hotspot_ratings_account_id'), 'hotspot_ratings', ['account_id'], unique=False)
    op.create_index(op.f('ix_hotspot_ratings_hotspot_id'), 'hotspot_ratings', ['hotspot_id'], unique=False)

    # A hotspot held at most one tag per dimension, so each (hotspot, author) gets at most one of each.
    op.execute(
        """
        INSERT INTO hotspot_ratings (hotspot_id, account_id, importance_tag_id, accuracy_tag_id, created_at, updated_at)
        SELECT hotspot_tags.hotspot_id, hotspot_tags.added_by_id,
               MAX(CASE WHEN tags.dimension = 'importance' THEN tags.id END),
               MAX(CASE WHEN tags.dimension = 'accuracy' THEN tags.id END),
               MIN(hotspot_tags.created_at), MAX(hotspot_tags.created_at)
        FROM hotspot_tags JOIN tags ON tags.id = hotspot_tags.tag_id
        WHERE tags.dimension IS NOT NULL AND hotspot_tags.added_by_id IS NOT NULL
        GROUP BY hotspot_tags.hotspot_id, hotspot_tags.added_by_id
        """
    )
    op.execute(f"DELETE FROM hotspot_tags WHERE tag_id IN ({CORE_TAG_IDS})")
