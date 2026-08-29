"""add calibre_book_links table for local metadata overlay

Revision ID: 041
Revises: 040
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '041'
down_revision = '040'
branch_labels = None
depends_on = None


def table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if table_exists('calibre_book_links'):
        return
    op.create_table(
        'calibre_book_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('calibre_book_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(), nullable=False, server_default='fuzzy'),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('confirmed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('calibre_isbn', sa.String(), nullable=True),
        sa.Column('calibre_title', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('calibre_book_id'),
    )
    op.create_index(
        'ix_calibre_book_links_calibre_book_id', 'calibre_book_links', ['calibre_book_id']
    )
    op.create_index(
        'ix_calibre_book_links_book_id', 'calibre_book_links', ['book_id']
    )


def downgrade() -> None:
    if not table_exists('calibre_book_links'):
        return
    op.drop_index('ix_calibre_book_links_book_id', table_name='calibre_book_links')
    op.drop_index('ix_calibre_book_links_calibre_book_id', table_name='calibre_book_links')
    op.drop_table('calibre_book_links')
