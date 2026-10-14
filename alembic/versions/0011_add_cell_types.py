"""Add cell types table

Revision ID: 0011_add_cell_types
Revises: 0010_slide_uploaded_by
Create Date: 2026-10-12 23:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011_add_cell_types'
down_revision: Union[str, Sequence[str], None] = '0010_slide_uploaded_by'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'cell_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('color', sa.String(length=7), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['category_id'],
            ['categories.id'],
            name=op.f('fk_cell_types_category_id_categories'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cell_types')),
    )
    op.create_index(op.f('ix_cell_types_category_id'), 'cell_types', ['category_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_cell_types_category_id'), table_name='cell_types')
    op.drop_table('cell_types')
