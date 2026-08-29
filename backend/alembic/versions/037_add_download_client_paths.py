"""Add ebook/audiobook download paths to download_clients

Revision ID: 037
Revises: 036
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '037'
down_revision = '036'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in the table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists('download_clients', 'ebook_download_path'):
        op.add_column('download_clients', sa.Column('ebook_download_path', sa.String(), nullable=True))
    if not column_exists('download_clients', 'audiobook_download_path'):
        op.add_column('download_clients', sa.Column('audiobook_download_path', sa.String(), nullable=True))


def downgrade() -> None:
    if column_exists('download_clients', 'audiobook_download_path'):
        op.drop_column('download_clients', 'audiobook_download_path')
    if column_exists('download_clients', 'ebook_download_path'):
        op.drop_column('download_clients', 'ebook_download_path')
