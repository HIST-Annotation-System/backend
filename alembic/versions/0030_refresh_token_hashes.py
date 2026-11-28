"""refresh tokens: store a SHA-256 of each token instead of the token, and when it was revoked instead of a flag

Existing sessions survive: their tokens are hashed in place (the hex SHA-256 of the token's UTF-8 bytes, the same as
`AuthService.hash_refresh_token`).

Revision ID: 0030_refresh_token_hashes
Revises: 0029_global_slides
Create Date: 2026-11-28 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0030_refresh_token_hashes'
down_revision: Union[str, Sequence[str], None] = '0029_global_slides'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f('ix_refresh_tokens_token'), table_name='refresh_tokens')
    op.alter_column('refresh_tokens', 'token', new_column_name='token_hash')
    op.execute("UPDATE refresh_tokens SET token_hash = encode(sha256(convert_to(token_hash, 'UTF8')), 'hex')")
    op.create_index(op.f('ix_refresh_tokens_token_hash'), 'refresh_tokens', ['token_hash'], unique=True)

    op.add_column('refresh_tokens', sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE refresh_tokens SET revoked_at = now() WHERE is_revoked")
    op.drop_column('refresh_tokens', 'is_revoked')

    op.create_index(op.f('ix_refresh_tokens_account_id'), 'refresh_tokens', ['account_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema. The tokens can't be recovered from their hashes, so every session ends: users sign in again."""
    op.execute("DELETE FROM refresh_tokens")
    op.drop_index(op.f('ix_refresh_tokens_account_id'), table_name='refresh_tokens')

    op.add_column(
        'refresh_tokens', sa.Column('is_revoked', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.alter_column('refresh_tokens', 'is_revoked', server_default=None)
    op.drop_column('refresh_tokens', 'revoked_at')

    op.drop_index(op.f('ix_refresh_tokens_token_hash'), table_name='refresh_tokens')
    op.alter_column('refresh_tokens', 'token_hash', new_column_name='token')
    op.create_index(op.f('ix_refresh_tokens_token'), 'refresh_tokens', ['token'], unique=True)
