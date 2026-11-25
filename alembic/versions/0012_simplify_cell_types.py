"""Simplify cell_types, link annotations to cell_types, and drop annotation_classes

Revision ID: 0012_simplify_cell_types
Revises: 0011_add_cell_types
Create Date: 2026-10-14 19:00:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012_simplify_cell_types'
down_revision: Union[str, Sequence[str], None] = '0011_add_cell_types'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Simplify cell_types: drop category_id FK and column, drop color column, add unique on name
    op.drop_index(op.f('ix_cell_types_category_id'), table_name='cell_types')
    op.drop_constraint(
        op.f('fk_cell_types_category_id_categories'), table_name='cell_types', type_='foreignkey'
    )
    op.drop_column('cell_types', 'color')
    op.drop_column('cell_types', 'category_id')
    op.create_unique_constraint(op.f('uq_cell_types_name'), 'cell_types', ['name'])

    # 2. Seed existing annotation_classes into cell_types
    classes = conn.execute(sa.text("SELECT id, name FROM annotation_classes")).fetchall()
    for _class_id, class_name in classes:
        existing = conn.execute(
            sa.text("SELECT id FROM cell_types WHERE name = :name"),
            {"name": class_name},
        ).scalar()
        if not existing:
            conn.execute(
                sa.text("INSERT INTO cell_types (name) VALUES (:name)"),
                {"name": class_name},
            )

    # 3. Update annotations table: add cell_type_id FK, migrate data from class_id, drop class_id
    op.add_column('annotations', sa.Column('cell_type_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_annotations_cell_type_id'), 'annotations', ['cell_type_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_annotations_cell_type_id_cell_types'),
        'annotations',
        'cell_types',
        ['cell_type_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    conn.execute(sa.text("""
        UPDATE annotations a
        SET cell_type_id = ct.id
        FROM annotation_classes ac
        JOIN cell_types ct ON ct.name = ac.name
        WHERE a.class_id = ac.id
    """))
    op.drop_constraint(op.f('fk_annotations_class_id_annotation_classes'), 'annotations', type_='foreignkey')
    op.drop_column('annotations', 'class_id')

    # 4. Update hotspot_annotations table: add cell_type_id FK, migrate data from class_id, drop class_id
    op.add_column('hotspot_annotations', sa.Column('cell_type_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_hotspot_annotations_cell_type_id'), 'hotspot_annotations', ['cell_type_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_hotspot_annotations_cell_type_id_cell_types'),
        'hotspot_annotations',
        'cell_types',
        ['cell_type_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    conn.execute(sa.text("""
        UPDATE hotspot_annotations ha
        SET cell_type_id = ct.id
        FROM annotation_classes ac
        JOIN cell_types ct ON ct.name = ac.name
        WHERE ha.class_id = ac.id
    """))
    op.drop_constraint(op.f('fk_hotspot_annotations_class_id_annotation_classes'), 'hotspot_annotations', type_='foreignkey')
    op.drop_column('hotspot_annotations', 'class_id')

    # 5. Drop annotation_classes table
    op.drop_table('annotation_classes')


def downgrade() -> None:
    conn = op.get_bind()

    # 1. Recreate annotation_classes table
    op.create_table(
        'annotation_classes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('color', sa.String(length=7), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_annotation_classes')),
    )

    # 2. Migrate cell_types back to annotation_classes
    cell_types = conn.execute(sa.text("SELECT id, name FROM cell_types")).fetchall()
    ct_to_class = {}
    for idx, (ct_id, name) in enumerate(cell_types, start=1):
        class_uuid = uuid.uuid4()
        ct_to_class[ct_id] = class_uuid
        conn.execute(
            sa.text("INSERT INTO annotation_classes (id, name, color, sort_order) VALUES (:id, :name, :color, :sort_order)"),
            {"id": class_uuid, "name": name, "color": "#3b82f6", "sort_order": idx},
        )

    # 3. Restore class_id on annotations
    op.add_column('annotations', sa.Column('class_id', sa.Uuid(), nullable=True))
    for ct_id, class_uuid in ct_to_class.items():
        conn.execute(
            sa.text("UPDATE annotations SET class_id = :class_id WHERE cell_type_id = :ct_id"),
            {"class_id": class_uuid, "ct_id": ct_id},
        )
    op.alter_column('annotations', 'class_id', nullable=False)
    op.create_foreign_key(
        op.f('fk_annotations_class_id_annotation_classes'),
        'annotations',
        'annotation_classes',
        ['class_id'],
        ['id'],
    )
    op.drop_constraint(op.f('fk_annotations_cell_type_id_cell_types'), 'annotations', type_='foreignkey')
    op.drop_index(op.f('ix_annotations_cell_type_id'), table_name='annotations')
    op.drop_column('annotations', 'cell_type_id')

    # 4. Restore class_id on hotspot_annotations
    op.add_column('hotspot_annotations', sa.Column('class_id', sa.Uuid(), nullable=True))
    for ct_id, class_uuid in ct_to_class.items():
        conn.execute(
            sa.text("UPDATE hotspot_annotations SET class_id = :class_id WHERE cell_type_id = :ct_id"),
            {"class_id": class_uuid, "ct_id": ct_id},
        )
    op.alter_column('hotspot_annotations', 'class_id', nullable=False)
    op.create_foreign_key(
        op.f('fk_hotspot_annotations_class_id_annotation_classes'),
        'hotspot_annotations',
        'annotation_classes',
        ['class_id'],
        ['id'],
    )
    op.drop_constraint(op.f('fk_hotspot_annotations_cell_type_id_cell_types'), 'hotspot_annotations', type_='foreignkey')
    op.drop_index(op.f('ix_hotspot_annotations_cell_type_id'), table_name='hotspot_annotations')
    op.drop_column('hotspot_annotations', 'cell_type_id')

    # 5. Restore color and category_id on cell_types
    op.drop_constraint(op.f('uq_cell_types_name'), 'cell_types', type_='unique')
    op.add_column('cell_types', sa.Column('category_id', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('cell_types', sa.Column('color', sa.String(length=7), nullable=False, server_default='#AAAAAA'))
    op.create_foreign_key(
        op.f('fk_cell_types_category_id_categories'),
        'cell_types', 'categories',
        ['category_id'], ['id'],
        ondelete='CASCADE',
    )
    op.create_index(op.f('ix_cell_types_category_id'), 'cell_types', ['category_id'], unique=False)
