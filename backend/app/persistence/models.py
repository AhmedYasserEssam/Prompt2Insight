from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ConnectionProfileRecord(Base):
    __tablename__ = "connection_profiles"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    dialect: Mapped[str] = mapped_column(String(20))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    database_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    credential_reference: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationRecord(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("connection_profiles.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogRevisionRecord(Base):
    __tablename__ = "catalog_revisions"
    __table_args__ = (
        UniqueConstraint(
            "connection_profile_id", "content_hash", "schema_snapshot_id",
            name="uq_catalog_revision_profile_content_snapshot",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    connection_profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("connection_profiles.id")
    )
    schema_snapshot_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("schema_snapshots.id")
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SchemaSnapshotRecord(Base):
    __tablename__ = "schema_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    connection_profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("connection_profiles.id")
    )
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalyticalRequestRecord(Base):
    __tablename__ = "analytical_requests"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("conversations.id")
    )
    question: Mapped[str] = mapped_column(Text)
    response_language: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(32))
    response: Mapped[dict[str, object]] = mapped_column(JSON)
    catalog_revision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("catalog_revisions.id")
    )
    schema_snapshot_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("schema_snapshots.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QueryExecutionRecord(Base):
    __tablename__ = "query_executions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analytical_requests.id")
    )
    status: Mapped[str] = mapped_column(String(32))
    sql_hash: Mapped[str | None] = mapped_column(String(64))
    validated_sql: Mapped[str | None] = mapped_column(Text)
    dialect: Mapped[str | None] = mapped_column(String(20))
    plan_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    row_count: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    model_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
