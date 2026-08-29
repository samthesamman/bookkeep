"""add book_delivery_email to users and email_logs table

Revision ID: 039
Revises: 038
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '039'
down_revision = '038'
branch_labels = None
depends_on = None


def table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists('users', 'book_delivery_email'):
        op.add_column('users', sa.Column('book_delivery_email', sa.String(), nullable=True))

    if not table_exists('email_logs'):
        op.create_table(
            'email_logs',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('recipient', sa.String(), nullable=False),
            sa.Column('subject', sa.String(), nullable=True),
            sa.Column('book_title', sa.String(), nullable=True),
            sa.Column('book_format', sa.String(), nullable=True),
            sa.Column('status', sa.String(), nullable=False, server_default='success'),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index('ix_email_logs_user_id', 'email_logs', ['user_id'])


def downgrade() -> None:
    if table_exists('email_logs'):
        op.drop_index('ix_email_logs_user_id', table_name='email_logs')
        op.drop_table('email_logs')
    if column_exists('users', 'book_delivery_email'):
        op.drop_column('users', 'book_delivery_email')
