"""add calibre_import_attempts table

Cooldown marker for Calibre library books that import_calibre_books searched
Hardcover + Open Library for and found nothing, so the same unmatchable book
isn't re-searched against both APIs on every run. See
_import_unlinked_calibre_books / CALIBRE_IMPORT_RETRY_COOLDOWN in app/tasks.py.

Revision ID: 049
Revises: 048
Create Date: 2026-09-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    if not table_exists("calibre_import_attempts"):
        op.create_table(
            "calibre_import_attempts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("calibre_book_id", sa.Integer(), nullable=False),
            sa.Column("last_attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_calibre_import_attempts_id"), "calibre_import_attempts", ["id"], unique=False
        )
        op.create_index(
            op.f("ix_calibre_import_attempts_calibre_book_id"),
            "calibre_import_attempts",
            ["calibre_book_id"],
            unique=True,
        )


def downgrade() -> None:
    if table_exists("calibre_import_attempts"):
        if index_exists("calibre_import_attempts", "ix_calibre_import_attempts_calibre_book_id"):
            op.drop_index(
                op.f("ix_calibre_import_attempts_calibre_book_id"),
                table_name="calibre_import_attempts",
            )
        if index_exists("calibre_import_attempts", "ix_calibre_import_attempts_id"):
            op.drop_index(op.f("ix_calibre_import_attempts_id"), table_name="calibre_import_attempts")
        op.drop_table("calibre_import_attempts")
