"""hotspot segmentation: status of the model's run on a hotspot, and which annotations it produced

Revision ID: 0020_hotspot_segmentation
Revises: 0019_annotation_kind
Create Date: 2026-11-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0020_hotspot_segmentation'
down_revision: Union[str, Sequence[str], None] = '0019_annotation_kind'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (additive: code that does not know the new columns keeps working)."""
    # Batch mode, so SQLite (which cannot add a constraint to an existing table) gets the CHECKs too.
    with op.batch_alter_table('hotspots') as batch_op:
        batch_op.add_column(
            sa.Column('segmentation_status', sa.String(length=16), server_default='pending', nullable=False)
        )
        batch_op.add_column(sa.Column('segmentation_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('segmentation_requested_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint(
            op.f('ck_hotspots_segmentation_status'),
            "segmentation_status IN ('pending', 'done', 'failed', 'skipped')",
        )
        batch_op.create_index(op.f('ix_hotspots_segmentation_status'), ['segmentation_status'], unique=False)
    with op.batch_alter_table('hotspot_annotations') as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=16), server_default='human', nullable=False))
        batch_op.add_column(sa.Column('model_version', sa.String(length=128), nullable=True))
        batch_op.create_check_constraint(
            op.f('ck_hotspot_annotations_annotation_source'), "source IN ('human', 'model')"
        )

    # Work already started by hand must not get model cells on top of it. All other hotspots stay pending, so the
    # worker backfills them.
    op.execute(
        """
        UPDATE hotspots SET segmentation_status = 'skipped'
        WHERE reannotated_at IS NOT NULL
           OR EXISTS (
               SELECT 1 FROM hotspot_annotations
               WHERE hotspot_annotations.hotspot_id = hotspots.id AND hotspot_annotations.kind = 'cell'
           )
        """
    )
    # The cell type of every model cell: the model finds outlines, it does not classify them.
    op.execute(
        """
        INSERT INTO cell_types (name)
        SELECT 'Unclassified'
        WHERE NOT EXISTS (SELECT 1 FROM cell_types WHERE lower(name) = 'unclassified')
        """
    )


def downgrade() -> None:
    """Downgrade schema. Untouched model cells go too: without `source` they would pass for human annotations."""
    op.execute("DELETE FROM hotspot_annotations WHERE source = 'model'")
    op.execute(
        """
        DELETE FROM cell_types
        WHERE lower(name) = 'unclassified'
          AND NOT EXISTS (SELECT 1 FROM hotspot_annotations WHERE hotspot_annotations.cell_type_id = cell_types.id)
        """
    )
    with op.batch_alter_table('hotspot_annotations') as batch_op:
        batch_op.drop_constraint(op.f('ck_hotspot_annotations_annotation_source'), type_='check')
        batch_op.drop_column('model_version')
        batch_op.drop_column('source')
    with op.batch_alter_table('hotspots') as batch_op:
        batch_op.drop_index(op.f('ix_hotspots_segmentation_status'))
        batch_op.drop_constraint(op.f('ck_hotspots_segmentation_status'), type_='check')
        batch_op.drop_column('segmentation_requested_at')
        batch_op.drop_column('segmentation_error')
        batch_op.drop_column('segmentation_status')
