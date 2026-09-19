"""remove booklore integration

Drops the booklore_servers table and the booklore_id/booklore_added_on
tracking columns on books. The Booklore integration itself (router, jobs,
UI) has been removed from the app; this is the corresponding schema cleanup.

Revision ID: 047
Revises: 046
Create Date: 2026-09-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if index_exists("books", "ix_books_booklore_id"):
        op.drop_index(op.f("ix_books_booklore_id"), table_name="books")
    if column_exists("books", "booklore_added_on"):
        op.drop_column("books", "booklore_added_on")
    if column_exists("books", "booklore_id"):
        op.drop_column("books", "booklore_id")

    if table_exists("booklore_servers"):
        if index_exists("booklore_servers", "ix_booklore_servers_id"):
            op.drop_index(op.f("ix_booklore_servers_id"), table_name="booklore_servers")
        op.drop_table("booklore_servers")


def downgrade() -> None:
    if not table_exists("booklore_servers"):
        op.create_table(
            "booklore_servers",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("password", sa.String(), nullable=False),
            sa.Column("is_default", sa.Boolean(), default=False),
            sa.Column("ebook_library_id", sa.Integer(), nullable=True),
            sa.Column("audiobook_library_id", sa.Integer(), nullable=True),
            sa.Column("access_token", sa.Text(), nullable=True),
            sa.Column("refresh_token", sa.Text(), nullable=True),
            sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_booklore_servers_id"), "booklore_servers", ["id"], unique=False)

    if not column_exists("books", "booklore_id"):
        op.add_column("books", sa.Column("booklore_id", sa.Integer(), nullable=True))
    if not column_exists("books", "booklore_added_on"):
        op.add_column("books", sa.Column("booklore_added_on", sa.DateTime(timezone=True), nullable=True))
    if not index_exists("books", "ix_books_booklore_id"):
        op.create_index(op.f("ix_books_booklore_id"), "books", ["booklore_id"], unique=True)
