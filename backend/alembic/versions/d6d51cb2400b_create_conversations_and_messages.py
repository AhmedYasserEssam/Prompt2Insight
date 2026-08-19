"""create_conversations_and_messages

Revision ID: d6d51cb2400b
Revises: 0003_catalog_schema_link
Create Date: 2026-08-19 19:06:33.673982
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d6d51cb2400b"
down_revision = "0003_catalog_schema_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The parent revision already owns the minimal conversations table.  Add the
    # Phase 2.2 columns before creating its dependent messages table.
    op.add_column(
        "conversations",
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("'New conversation'"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "language", sa.String(length=10), nullable=False, server_default=sa.text("'auto'")
        ),
    )
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column(
            "context_state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column("conversations", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])
    op.alter_column("conversations", "title", server_default=None)
    op.alter_column("conversations", "language", server_default=None)
    op.alter_column("conversations", "context_state", server_default=None)

    # The parent migration used PostgreSQL's implicit name.  Replace it with
    # the stable name declared by the model.
    op.drop_constraint(
        "conversations_connection_profile_id_fkey", "conversations", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_conversations_connection_profile_id",
        "conversations",
        "connection_profiles",
        ["connection_profile_id"],
        ["id"],
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name="ck_messages_role_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_messages_conversation_sequence_number",
        ),
    )
    op.create_index(
        "ix_messages_conversation_id_sequence_number",
        "messages",
        ["conversation_id", "sequence_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_id_sequence_number", table_name="messages")
    op.drop_table("messages")

    op.drop_constraint(
        "fk_conversations_connection_profile_id", "conversations", type_="foreignkey"
    )
    op.create_foreign_key(
        None, "conversations", "connection_profiles", ["connection_profile_id"], ["id"]
    )
    op.drop_index("ix_conversations_updated_at", table_name="conversations")
    op.drop_column("conversations", "archived_at")
    op.drop_column("conversations", "updated_at")
    op.drop_column("conversations", "context_state")
    op.drop_column("conversations", "summary")
    op.drop_column("conversations", "language")
    op.drop_column("conversations", "title")
