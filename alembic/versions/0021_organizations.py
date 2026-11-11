"""organizations: every account (except system admins), slide and hotspot folder belongs to one

Revision ID: 0021_organizations
Revises: 0020_hotspot_segmentation
Create Date: 2026-11-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0021_organizations'
down_revision: Union[str, Sequence[str], None] = '0020_hotspot_segmentation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Everything that exists before organizations goes to this one.
DEFAULT_ORGANIZATION = 'HistAno'


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == 'postgresql'


def upgrade() -> None:
    """Upgrade schema. System admins (role ADMIN) keep no organization."""
    if _is_postgresql():
        # `role` is a native enum on PostgreSQL (a plain VARCHAR elsewhere). The new value is not used below, so
        # adding it inside the migration's transaction is fine (PostgreSQL 12+).
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'ORG_ADMIN'")

    op.create_table(
        'organizations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations')),
        sa.UniqueConstraint('name', name=op.f('uq_organizations_name')),
    )
    op.execute(f"INSERT INTO organizations (name) VALUES ('{DEFAULT_ORGANIZATION}')")
    default_org = f"(SELECT id FROM organizations WHERE name = '{DEFAULT_ORGANIZATION}')"

    with op.batch_alter_table('accounts') as batch_op:
        batch_op.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f('fk_accounts_organization_id_organizations'), 'organizations', ['organization_id'], ['id'],
            ondelete='RESTRICT',
        )
        batch_op.create_index(op.f('ix_accounts_organization_id'), ['organization_id'], unique=False)
    op.execute(f"UPDATE accounts SET organization_id = {default_org} WHERE role <> 'ADMIN'")

    for table in ('slides', 'hotspot_folders'):
        op.add_column(table, sa.Column('organization_id', sa.Integer(), nullable=True))
        op.execute(f"UPDATE {table} SET organization_id = {default_org}")
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column('organization_id', existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(
                op.f(f'fk_{table}_organization_id_organizations'), 'organizations', ['organization_id'], ['id'],
                ondelete='RESTRICT',
            )
            batch_op.create_index(op.f(f'ix_{table}_organization_id'), ['organization_id'], unique=False)

    # Folder names only need to be unique within an organization.
    with op.batch_alter_table('hotspot_folders') as batch_op:
        batch_op.drop_constraint(op.f('uq_hotspot_folders_name'), type_='unique')
        batch_op.create_unique_constraint(
            op.f('uq_hotspot_folders_organization_id'), ['organization_id', 'name']
        )


def downgrade() -> None:
    """Downgrade schema. Organization admins become annotators; fails if two organizations share a folder name."""
    with op.batch_alter_table('hotspot_folders') as batch_op:
        batch_op.drop_constraint(op.f('uq_hotspot_folders_organization_id'), type_='unique')
        batch_op.create_unique_constraint(op.f('uq_hotspot_folders_name'), ['name'])

    for table in ('hotspot_folders', 'slides', 'accounts'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(op.f(f'ix_{table}_organization_id'))
            batch_op.drop_constraint(op.f(f'fk_{table}_organization_id_organizations'), type_='foreignkey')
            batch_op.drop_column('organization_id')
    op.drop_table('organizations')

    op.execute("UPDATE accounts SET role = 'ANNOTATOR' WHERE role = 'ORG_ADMIN'")
    if _is_postgresql():
        # PostgreSQL can't drop a value from an enum: the type is rebuilt without it.
        op.execute("ALTER TYPE role RENAME TO role_old")
        op.execute("CREATE TYPE role AS ENUM ('ADMIN', 'ANNOTATOR')")
        op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE role USING role::text::role")
        op.execute("DROP TYPE role_old")
