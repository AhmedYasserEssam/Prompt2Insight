"""Create application persistence tables.

Revision ID: 0001_persistence
Revises:
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_persistence"
down_revision = None
branch_labels = None
depends_on = None


def uuid_column() -> sa.Uuid:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "connection_profiles",
        sa.Column("id", uuid_column(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False, unique=True),
        sa.Column("dialect", sa.String(length=20), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database_name", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("credential_reference", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "conversations",
        sa.Column("id", uuid_column(), primary_key=True),
        sa.Column("connection_profile_id", uuid_column(), sa.ForeignKey("connection_profiles.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "catalog_revisions",
        sa.Column("id", uuid_column(), primary_key=True),
        sa.Column("connection_profile_id", uuid_column(), sa.ForeignKey("connection_profiles.id"), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "schema_snapshots",
        sa.Column("id", uuid_column(), primary_key=True),
        sa.Column("connection_profile_id", uuid_column(), sa.ForeignKey("connection_profiles.id"), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "analytical_requests",
        sa.Column("id", uuid_column(), primary_key=True),
        sa.Column("conversation_id", uuid_column(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("response_language", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("catalog_revision_id", uuid_column(), sa.ForeignKey("catalog_revisions.id")),
        sa.Column("schema_snapshot_id", uuid_column(), sa.ForeignKey("schema_snapshots.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "query_executions",
        sa.Column("id", uuid_column(), primary_key=True),
        sa.Column("request_id", uuid_column(), sa.ForeignKey("analytical_requests.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sql_hash", sa.String(length=64)),
        sa.Column("model_metadata", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("query_executions")
    op.drop_table("analytical_requests")
    op.drop_table("schema_snapshots")
    op.drop_table("catalog_revisions")
    op.drop_table("conversations")
    op.drop_table("connection_profiles")
