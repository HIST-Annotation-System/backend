"""strict slide formats

Slides without resolution (MPP) metadata - every plain JPEG/PNG/BMP image among them - are no longer
supported; existing ones are marked failed so they can be re-uploaded in a supported format.

Revision ID: 0004_strict_slide_formats
Revises: e15f784d36a5
Create Date: 2026-09-17 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0004_strict_slide_formats'
down_revision: Union[str, Sequence[str], None] = 'e15f784d36a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNSUPPORTED_SLIDE = (
    'This slide has no resolution (MPP) metadata or uses a format that is no longer supported. '
    'Re-upload it in a supported format.'
)

# Self-contained snapshot of the table (migrations must not import application models).
slides = sa.table(
    'slides',
    sa.column('status', sa.String()),
    sa.column('error_message', sa.Text()),
    sa.column('mpp_x', sa.Float()),
)


def upgrade() -> None:
    """Fail ready slides that have no MPP."""
    op.execute(
        slides.update()
        .where(slides.c.status == 'ready', slides.c.mpp_x.is_(None))
        .values(status='failed', error_message=UNSUPPORTED_SLIDE)
    )


def downgrade() -> None:
    """Nothing to restore: the slides stay failed."""
