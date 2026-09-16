"""add calibre agent settings columns

Revision ID: 046
Revises: 045
Create Date: 2026-09-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '046'
down_revision = '045'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    inspector = inspect(op.get_bind())
    return column_name in [col["name"] for col in inspector.get_columns(table_name)]


def upgrade() -> None:
    if not column_exists('calibre_settings', 'agent_enabled'):
        op.add_column(
            'calibre_settings',
            sa.Column('agent_enabled', sa.Boolean(), server_default='false'),
        )
    if not column_exists('calibre_settings', 'agent_url'):
        op.add_column('calibre_settings', sa.Column('agent_url', sa.String(), nullable=True))
    if not column_exists('calibre_settings', 'agent_api_key'):
        op.add_column('calibre_settings', sa.Column('agent_api_key', sa.String(), nullable=True))
    if not column_exists('calibre_settings', 'agent_convert_format'):
        op.add_column(
            'calibre_settings', sa.Column('agent_convert_format', sa.String(), nullable=True)
        )


def downgrade() -> None:
    if column_exists('calibre_settings', 'agent_convert_format'):
        op.drop_column('calibre_settings', 'agent_convert_format')
    if column_exists('calibre_settings', 'agent_api_key'):
        op.drop_column('calibre_settings', 'agent_api_key')
    if column_exists('calibre_settings', 'agent_url'):
        op.drop_column('calibre_settings', 'agent_url')
    if column_exists('calibre_settings', 'agent_enabled'):
        op.drop_column('calibre_settings', 'agent_enabled')
