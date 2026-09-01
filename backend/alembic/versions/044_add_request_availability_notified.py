"""add availability_notified_at to book_requests

Revision ID: 044
Revises: 043
Create Date: 2026-09-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '044'
down_revision = '043'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if column_exists('book_requests', 'availability_notified_at'):
        return
    op.add_column(
        'book_requests',
        sa.Column('availability_notified_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: requests that are already available (or were previously handled by
    # the old auto-email flow) must not trigger a burst of "now available" emails
    # on the first scheduler run after deploy. Mark them as already notified.
    op.execute(
        "UPDATE book_requests "
        "SET availability_notified_at = CURRENT_TIMESTAMP "
        "WHERE status = 'available' OR auto_email_sent_at IS NOT NULL"
    )


def downgrade() -> None:
    if column_exists('book_requests', 'availability_notified_at'):
        op.drop_column('book_requests', 'availability_notified_at')
