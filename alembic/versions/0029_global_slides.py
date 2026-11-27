"""global slides: a slide with no organization is seen by every organization (only the system admin adds them)

Revision ID: 0029_global_slides
Revises: 0028_organization_tags
Create Date: 2026-11-27 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0029_global_slides'
down_revision: Union[str, Sequence[str], None] = '0028_organization_tags'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('slides', 'organization_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Downgrade schema (fails while global slides exist: give them an organization or delete them first)."""
    op.alter_column('slides', 'organization_id', existing_type=sa.Integer(), nullable=False)
