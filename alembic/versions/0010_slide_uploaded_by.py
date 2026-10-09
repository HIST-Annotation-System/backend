"""slide uploaded_by: the admin who uploaded or imported a slide

Revision ID: 0010_slide_uploaded_by
Revises: 0009_hotspot_features
Create Date: 2026-10-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0010_slide_uploaded_by'
down_revision: Union[str, Sequence[str], None] = '0009_hotspot_features'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (existing slides keep no uploader)."""
    op.add_column('slides', sa.Column('uploaded_by_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_slides_uploaded_by_id'), 'slides', ['uploaded_by_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_slides_uploaded_by_id_accounts'), 'slides', 'accounts', ['uploaded_by_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_slides_uploaded_by_id_accounts'), 'slides', type_='foreignkey')
    op.drop_index(op.f('ix_slides_uploaded_by_id'), table_name='slides')
    op.drop_column('slides', 'uploaded_by_id')
