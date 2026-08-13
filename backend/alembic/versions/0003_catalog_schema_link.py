"""Link catalog revisions to their validated schema snapshot.

Revision ID: 0003_catalog_schema_link
Revises: 0002_execution_provenance
Create Date: 2026-08-13
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_catalog_schema_link"
down_revision = "0002_execution_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_revisions",
        sa.Column("schema_snapshot_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_catalog_revisions_schema_snapshot_id",
        "catalog_revisions",
        "schema_snapshots",
        ["schema_snapshot_id"],
        ["id"],
    )
    op.drop_constraint("catalog_revisions_content_hash_key", "catalog_revisions", type_="unique")
    op.create_unique_constraint(
        "uq_catalog_revision_profile_content_snapshot",
        "catalog_revisions",
        ["connection_profile_id", "content_hash", "schema_snapshot_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_catalog_revision_profile_content_snapshot", "catalog_revisions", type_="unique"
    )
    op.create_unique_constraint(
        "catalog_revisions_content_hash_key", "catalog_revisions", ["content_hash"]
    )
    op.drop_constraint(
        "fk_catalog_revisions_schema_snapshot_id", "catalog_revisions", type_="foreignkey"
    )
    op.drop_column("catalog_revisions", "schema_snapshot_id")
