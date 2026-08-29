"""add calibre_settings table

Revision ID: 038
Revises: 037
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '038'
down_revision = '037'
branch_labels = None
depends_on = None


def table_exists(table_name: str) -> bool:
    """Check if a table exists."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not table_exists('calibre_settings'):
        op.create_table(
            'calibre_settings',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('library_path', sa.String(), nullable=True),
            sa.Column('enabled', sa.Boolean(), server_default='false'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if table_exists('calibre_settings'):
        op.drop_table('calibre_settings')
