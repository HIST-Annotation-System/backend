"""tags belong to an organization, or to everyone (global) when the system admin created them

Existing tags become global, so nothing changes for what organizations see until they add tags of their own.

Revision ID: 0028_organization_tags
Revises: 0027_model_confidence
Create Date: 2026-11-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0028_organization_tags'
down_revision: Union[str, Sequence[str], None] = '0027_model_confidence'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tags', sa.Column('organization_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_tags_organization_id_organizations'), 'tags', 'organizations', ['organization_id'], ['id'],
        ondelete='CASCADE',
    )
    op.create_index(op.f('ix_tags_organization_id'), 'tags', ['organization_id'], unique=False)
    op.drop_constraint(op.f('uq_tags_name'), 'tags', type_='unique')
    op.create_index(
        'uq_tags_global_name', 'tags', ['name'], unique=True, postgresql_where=sa.text('organization_id IS NULL')
    )
    op.create_index('uq_tags_organization_name', 'tags', ['organization_id', 'name'], unique=True)


def downgrade() -> None:
    """Downgrade schema (deletes organization tags: their names may clash once they are all global again)."""
    op.execute('DELETE FROM tags WHERE organization_id IS NOT NULL')
    op.drop_index('uq_tags_organization_name', table_name='tags')
    op.drop_index('uq_tags_global_name', table_name='tags')
    op.create_unique_constraint(op.f('uq_tags_name'), 'tags', ['name'])
    op.drop_index(op.f('ix_tags_organization_id'), table_name='tags')
    op.drop_constraint(op.f('fk_tags_organization_id_organizations'), 'tags', type_='foreignkey')
    op.drop_column('tags', 'organization_id')
