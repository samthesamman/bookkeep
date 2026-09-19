"""add metadata_sync_exhausted_at to books

Revision ID: 050
Revises: 049
Create Date: 2026-09-19

Cooldown marker for a Book that sync_calibre_metadata / sync_audiobook_metadata
searched every configured source for and found nothing new (a clean response,
not an API error) - so the same unmatchable book isn't re-searched on every
scheduled run forever. Cleared on the next enrichment that actually finds
something, or by an admin retry/manual link from the "Missing Metadata" page.
See app/services/book_metadata.py's MetadataEnrichmentError and the two sync
jobs in app/tasks.py.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists("books", "metadata_sync_exhausted_at"):
        op.add_column(
            "books",
            sa.Column("metadata_sync_exhausted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if column_exists("books", "metadata_sync_exhausted_at"):
        op.drop_column("books", "metadata_sync_exhausted_at")
