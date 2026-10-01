"""rename cancer annotation class

Renames the annotation class 'Nowotwór (Rak)' to the medically precise term
'Tkanka nowotworowa'. The seed migration (0002) already uses the new name;
this migration updates any databases that were created before the rename.

Revision ID: 0006_rename_cancer_class
Revises: 0005_two_tissue_classes
Create Date: 2026-10-01 22:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0006_rename_cancer_class'
down_revision: Union[str, Sequence[str], None] = '0005_two_tissue_classes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CANCER_CLASS_ID = uuid.UUID('938c0669-fefc-4efa-90c7-1dde449bab73')

annotation_classes = sa.table(
    'annotation_classes',
    sa.column('id', sa.Uuid()),
    sa.column('name', sa.String()),
)


def upgrade() -> None:
    """Rename 'Nowotwór (Rak)' → 'Tkanka nowotworowa'."""
    op.execute(
        annotation_classes.update()
        .where(annotation_classes.c.id == CANCER_CLASS_ID)
        .values(name='Tkanka nowotworowa')
    )


def downgrade() -> None:
    """Restore the old colloquial name."""
    op.execute(
        annotation_classes.update()
        .where(annotation_classes.c.id == CANCER_CLASS_ID)
        .values(name='Nowotwór (Rak)')
    )
