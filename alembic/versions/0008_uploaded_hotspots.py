"""uploaded hotspots: any image size, optional source slide, PNG or JPEG

Revision ID: 0008_uploaded_hotspots
Revises: 0007_hotspots
Create Date: 2026-10-04 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0008_uploaded_hotspots'
down_revision: Union[str, Sequence[str], None] = '0007_hotspots'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

hotspots = sa.table(
    'hotspots',
    sa.column('image_size', sa.Integer()),
    sa.column('image_width', sa.Integer()),
    sa.column('image_height', sa.Integer()),
)


def upgrade() -> None:
    """Upgrade schema."""
    # Captured squares so far: width = height = the old image_size.
    op.add_column('hotspots', sa.Column('image_width', sa.Integer(), nullable=True))
    op.add_column('hotspots', sa.Column('image_height', sa.Integer(), nullable=True))
    op.execute(hotspots.update().values(image_width=hotspots.c.image_size, image_height=hotspots.c.image_size))
    with op.batch_alter_table('hotspots') as batch:
        batch.alter_column('image_width', nullable=False)
        batch.alter_column('image_height', nullable=False)
        batch.drop_column('image_size')
        batch.add_column(
            sa.Column(
                'image_type',
                sa.Enum('png', 'jpeg', name='hotspot_image_type', native_enum=False, create_constraint=True, length=8),
                server_default='png',
                nullable=False,
            )
        )
        # Uploaded images have no source slide.
        batch.alter_column('slide_name', existing_type=sa.String(length=255), nullable=True)
        batch.alter_column('x', existing_type=sa.Integer(), nullable=True)
        batch.alter_column('y', existing_type=sa.Integer(), nullable=True)
        batch.alter_column('size', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Downgrade schema (uploaded hotspots cannot be represented and are deleted)."""
    op.execute("DELETE FROM hotspots WHERE size IS NULL")
    op.add_column('hotspots', sa.Column('image_size', sa.Integer(), nullable=True))
    op.execute(hotspots.update().values(image_size=hotspots.c.image_width))
    with op.batch_alter_table('hotspots') as batch:
        batch.alter_column('image_size', nullable=False)
        batch.drop_column('image_width')
        batch.drop_column('image_height')
        batch.drop_column('image_type')
        batch.alter_column('slide_name', existing_type=sa.String(length=255), nullable=False)
        batch.alter_column('x', existing_type=sa.Integer(), nullable=False)
        batch.alter_column('y', existing_type=sa.Integer(), nullable=False)
        batch.alter_column('size', existing_type=sa.Integer(), nullable=False)
