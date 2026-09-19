"""add admin_alerted_at to download_tasks

Tracks whether an admin has already been emailed about an ebook download
stuck in awaiting_library too long, so the alert fires once instead of every
reconcile run. See reconcile_ebook_library_imports / _ebook_library_wait_timeout
in app/tasks.py.

Revision ID: 048
Revises: 047
Create Date: 2026-09-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists("download_tasks", "admin_alerted_at"):
        op.add_column(
            "download_tasks",
            sa.Column("admin_alerted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if column_exists("download_tasks", "admin_alerted_at"):
        op.drop_column("download_tasks", "admin_alerted_at")
