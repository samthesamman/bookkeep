"""add auto-email fields to book_requests

Revision ID: 040
Revises: 039
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '040'
down_revision = '039'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists('book_requests', 'auto_email_when_available'):
        op.add_column(
            'book_requests',
            sa.Column('auto_email_when_available', sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not column_exists('book_requests', 'auto_email_sent_at'):
        op.add_column(
            'book_requests',
            sa.Column('auto_email_sent_at', sa.DateTime(timezone=True), nullable=True),
        )
    if not column_exists('book_requests', 'auto_email_attempts'):
        op.add_column(
            'book_requests',
            sa.Column('auto_email_attempts', sa.Integer(), nullable=False, server_default='0'),
        )


def downgrade() -> None:
    for col in ('auto_email_attempts', 'auto_email_sent_at', 'auto_email_when_available'):
        if column_exists('book_requests', col):
            op.drop_column('book_requests', col)
