"""add metadata_locked to books

Revision ID: 042
Revises: 041
Create Date: 2026-08-30

When an admin curates a book's metadata (the "choose source" picker, the
Calibre refresh/link actions), we set metadata_locked so a later Hardcover
detail fetch does not overwrite those fields back to Hardcover's version.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '042'
down_revision = '041'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists('books', 'metadata_locked'):
        op.add_column(
            'books',
            sa.Column(
                'metadata_locked', sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )


def downgrade() -> None:
    if column_exists('books', 'metadata_locked'):
        op.drop_column('books', 'metadata_locked')
