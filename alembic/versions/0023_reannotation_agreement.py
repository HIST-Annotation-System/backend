"""reannotation agreement: one layer per annotator, confirmed on their own, judged by their agreement

Hotspots that already hold annotations by people, or were marked reannotated, keep the old shared way (`layered`
false). The organizations' settings start at kappa with a threshold of 0.6.

Revision ID: 0023_reannotation_agreement
Revises: 0022_hotspot_ratings
Create Date: 2026-11-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0023_reannotation_agreement'
down_revision: Union[str, Sequence[str], None] = '0022_hotspot_ratings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def _metric_enum() -> sa.Enum:
    return sa.Enum('kappa', 'iou', name='agreement_metric', native_enum=False, create_constraint=True, length=8)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'organizations', sa.Column('agreement_metric', _metric_enum(), server_default='kappa', nullable=False)
    )
    op.add_column(
        'organizations', sa.Column('agreement_threshold', sa.Float(), server_default='0.6', nullable=False)
    )
    op.add_column('hotspots', sa.Column('layered', sa.Boolean(), server_default=sa.true(), nullable=False))
    op.execute(
        """
        UPDATE hotspots SET layered = false
        WHERE reannotated_at IS NOT NULL
           OR EXISTS (
               SELECT 1 FROM hotspot_annotations
               WHERE hotspot_annotations.hotspot_id = hotspots.id AND hotspot_annotations.source = 'human'
           )
        """
    )
    op.add_column('hotspot_annotations', sa.Column('origin_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_hotspot_annotations_origin_id'), 'hotspot_annotations', ['origin_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_hotspot_annotations_origin_id_hotspot_annotations'),
        'hotspot_annotations', 'hotspot_annotations', ['origin_id'], ['id'], ondelete='SET NULL',
    )

    op.create_table(
        'hotspot_reannotations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('hotspot_id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('draft', 'confirmed', name='reannotation_status', native_enum=False, create_constraint=True, length=16),
            nullable=False,
        ),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_hotspot_reannotations_account_id_accounts'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['hotspot_id'], ['hotspots.id'], name=op.f('fk_hotspot_reannotations_hotspot_id_hotspots'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_hotspot_reannotations')),
        sa.UniqueConstraint('hotspot_id', 'account_id', name=op.f('uq_hotspot_reannotations_hotspot_id')),
    )
    op.create_index(op.f('ix_hotspot_reannotations_account_id'), 'hotspot_reannotations', ['account_id'])
    op.create_index(op.f('ix_hotspot_reannotations_hotspot_id'), 'hotspot_reannotations', ['hotspot_id'])

    op.create_table(
        'hotspot_agreements',
        sa.Column('hotspot_id', sa.Uuid(), nullable=False),
        sa.Column('kappa', sa.Float(), nullable=True),
        sa.Column('iou', sa.Float(), nullable=True),
        sa.Column('per_annotator', JSON_TYPE, nullable=False),
        sa.Column('metric', _metric_enum(), nullable=False),
        sa.Column('threshold', sa.Float(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ['hotspot_id'], ['hotspots.id'], name=op.f('fk_hotspot_agreements_hotspot_id_hotspots'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('hotspot_id', name=op.f('pk_hotspot_agreements')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('hotspot_agreements')
    op.drop_index(op.f('ix_hotspot_reannotations_hotspot_id'), table_name='hotspot_reannotations')
    op.drop_index(op.f('ix_hotspot_reannotations_account_id'), table_name='hotspot_reannotations')
    op.drop_table('hotspot_reannotations')
    op.drop_constraint(
        op.f('fk_hotspot_annotations_origin_id_hotspot_annotations'), 'hotspot_annotations', type_='foreignkey'
    )
    op.drop_index(op.f('ix_hotspot_annotations_origin_id'), table_name='hotspot_annotations')
    op.drop_column('hotspot_annotations', 'origin_id')
    op.drop_column('hotspots', 'layered')
    op.drop_column('organizations', 'agreement_threshold')
    op.drop_column('organizations', 'agreement_metric')
