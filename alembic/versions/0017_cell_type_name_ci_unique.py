"""cell types: names unique ignoring letter case and extra whitespace

Revision ID: 0017_cell_type_name_ci_unique
Revises: 0016_hotspot_tags
Create Date: 2026-10-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0017_cell_type_name_ci_unique'
down_revision: Union[str, Sequence[str], None] = '0016_hotspot_tags'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Trimmed, inner whitespace collapsed and cut to the column length: what the API accepts (normalize_cell_type_name).
NORMALIZED_NAME = r"left(regexp_replace(btrim(name), '\s+', ' ', 'g'), 128)"


def upgrade() -> None:
    """Upgrade schema. Cell types that only differ in case/whitespace are merged into the oldest one."""
    conn = op.get_bind()
    duplicates = conn.execute(sa.text(f"""
        SELECT id, min(id) OVER (PARTITION BY lower({NORMALIZED_NAME})) AS keep_id
        FROM cell_types
    """)).fetchall()
    for cell_type_id, keep_id in duplicates:
        if cell_type_id == keep_id:
            continue
        conn.execute(
            sa.text("UPDATE hotspot_annotations SET cell_type_id = :keep_id WHERE cell_type_id = :id"),
            {"keep_id": keep_id, "id": cell_type_id},
        )
        conn.execute(sa.text("DELETE FROM cell_types WHERE id = :id"), {"id": cell_type_id})
    conn.execute(sa.text(f"UPDATE cell_types SET name = {NORMALIZED_NAME} WHERE name <> {NORMALIZED_NAME}"))

    op.alter_column('cell_types', 'name', type_=sa.String(length=128), existing_type=sa.String(), existing_nullable=False)
    op.create_index('uq_cell_types_name_lower', 'cell_types', [sa.text('lower(name)')], unique=True)


def downgrade() -> None:
    """Downgrade schema (merged cell types are not split again)."""
    op.drop_index('uq_cell_types_name_lower', table_name='cell_types')
    op.alter_column('cell_types', 'name', type_=sa.String(), existing_type=sa.String(length=128), existing_nullable=False)
