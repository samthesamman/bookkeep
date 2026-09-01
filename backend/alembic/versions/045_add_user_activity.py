"""add user activity tracking (last_seen_at + user_activity_days)

Revision ID: 045
Revises: 044
Create Date: 2026-09-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '045'
down_revision = '044'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    inspector = inspect(op.get_bind())
    return column_name in [col["name"] for col in inspector.get_columns(table_name)]


def table_exists(table_name: str) -> bool:
    inspector = inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not column_exists('users', 'last_seen_at'):
        op.add_column(
            'users',
            sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        )

    if not table_exists('user_activity_days'):
        op.create_table(
            'user_activity_days',
            sa.Column(
                'user_id',
                sa.Integer(),
                sa.ForeignKey('users.id', ondelete='CASCADE'),
                primary_key=True,
            ),
            sa.Column('day', sa.Date(), primary_key=True),
            sa.Column(
                'request_count', sa.Integer(), nullable=False, server_default='0'
            ),
        )
        op.create_index(
            'ix_user_activity_days_day', 'user_activity_days', ['day']
        )


def downgrade() -> None:
    if table_exists('user_activity_days'):
        op.drop_index('ix_user_activity_days_day', table_name='user_activity_days')
        op.drop_table('user_activity_days')
    if column_exists('users', 'last_seen_at'):
        op.drop_column('users', 'last_seen_at')
