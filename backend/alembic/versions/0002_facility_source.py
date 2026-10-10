"""Facilities from OpenStreetMap: source and external id.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('facilities') as batch:
        batch.add_column(sa.Column('source', sa.String(length=16), nullable=False, server_default='registry'))
        batch.add_column(sa.Column('external_id', sa.String(length=64), nullable=True))
        batch.create_unique_constraint('uq_facilities_external_id', ['external_id'])


def downgrade() -> None:
    with op.batch_alter_table('facilities') as batch:
        batch.drop_constraint('uq_facilities_external_id', type_='unique')
        batch.drop_column('external_id')
        batch.drop_column('source')
