"""Persist validated execution provenance.

Revision ID: 0002_execution_provenance
Revises: 0001_persistence
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_execution_provenance"
down_revision = "0001_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("query_executions", sa.Column("validated_sql", sa.Text(), nullable=True))
    op.add_column("query_executions", sa.Column("dialect", sa.String(length=20), nullable=True))
    op.add_column("query_executions", sa.Column("plan_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("query_executions", sa.Column("row_count", sa.Integer(), nullable=True))
    op.add_column("query_executions", sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("query_executions", sa.Column("error_code", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("query_executions", "error_code")
    op.drop_column("query_executions", "truncated")
    op.drop_column("query_executions", "row_count")
    op.drop_column("query_executions", "plan_metadata")
    op.drop_column("query_executions", "dialect")
    op.drop_column("query_executions", "validated_sql")
