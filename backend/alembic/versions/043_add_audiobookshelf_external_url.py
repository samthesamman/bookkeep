"""add external_url to audiobookshelf_servers

Revision ID: 043
Revises: 042
Create Date: 2026-08-30

The public/external URL an admin can set per Audiobookshelf server, used for
user-facing "Listen Now" deep links on book pages. Falls back to `url`.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '043'
down_revision = '042'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists('audiobookshelf_servers', 'external_url'):
        op.add_column(
            'audiobookshelf_servers',
            sa.Column('external_url', sa.String(), nullable=True),
        )


def downgrade() -> None:
    if column_exists('audiobookshelf_servers', 'external_url'):
        op.drop_column('audiobookshelf_servers', 'external_url')
