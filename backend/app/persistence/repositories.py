import json
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.analytics.run_analytics_request import PlanningContext
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnalyticsRequest, AnalyticsResponse
from app.domain.databases.connection_profiles import ConnectionProfileInput, ConnectionProfileView
from app.domain.databases.models import SchemaSnapshot, SQLDialect
from app.infrastructure.catalogs.models import AnalyticsCatalog
from app.persistence.models import (
    AnalyticalRequestRecord,
    CatalogRevisionRecord,
    ConnectionProfileRecord,
    ConversationRecord,
    QueryExecutionRecord,
    SchemaSnapshotRecord,
)


class AnalyticsRequestRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        async with self._session_factory() as session:
            record = await session.get(AnalyticalRequestRecord, request_id)
            if record is None:
                return None
            return AnalyticsResponse.model_validate(record.response)

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
        planning_context: PlanningContext | None = None,
    ) -> AnalyticsResponse:
        async with self._session_factory() as session:
            existing = await session.get(AnalyticalRequestRecord, request.request_id)
            if existing is not None:
                return AnalyticsResponse.model_validate(existing.response)

            if await session.get(ConversationRecord, conversation_id) is None:
                session.add(ConversationRecord(id=conversation_id))
                await session.flush()

            session.add(
                AnalyticalRequestRecord(
                    id=request.request_id,
                    conversation_id=conversation_id,
                    question=request.question,
                    response_language=request.response_language.value,
                    status=response.status.value,
                    response=response.model_dump(mode="json"),
                    catalog_revision_id=(
                        planning_context.catalog_revision_id
                        if planning_context is not None
                        else None
                    ),
                    schema_snapshot_id=(
                        planning_context.schema_snapshot_id
                        if planning_context is not None
                        else None
                    ),
                )
            )
            session.add(
                QueryExecutionRecord(
                    request_id=request.request_id,
                    status=response.status.value,
                    sql_hash=(sha256(response.sql.encode()).hexdigest() if response.sql else None),
                    validated_sql=response.sql,
                    dialect=(
                        response.query_plan.database_dialect.value if response.query_plan else None
                    ),
                    plan_metadata=(
                        {
                            "metric_ids": response.query_plan.metric_ids,
                            "dimension_ids": response.query_plan.dimension_ids,
                            "parameters": response.query_plan.parameter_bindings(),
                            "fallback_used": response.model_metadata.fallback_used
                            if response.model_metadata
                            else False,
                            "fallback_reason": response.model_metadata.fallback_reason
                            if response.model_metadata
                            else None,
                        }
                        if response.query_plan
                        else {}
                    ),
                    row_count=(response.table and len(response.table.rows)),
                    truncated=any("truncated" in warning.lower() for warning in response.warnings),
                    error_code=response.error_code.value if response.error_code else None,
                    latency_ms=(
                        response.model_metadata.latency_ms
                        if response.model_metadata is not None
                        and response.model_metadata.latency_ms is not None
                        else 0
                    ),
                    model_metadata=(
                        response.model_metadata.model_dump(mode="json")
                        if response.model_metadata is not None
                        else {}
                    ),
                )
            )
            await session.commit()
            return response

    async def get_planning_context(self, conversation_id: UUID) -> PlanningContext:
        async with self._session_factory() as session:
            conversation = await session.get(ConversationRecord, conversation_id)
            if conversation is None or conversation.connection_profile_id is None:
                raise Prompt2InsightError(
                    ErrorCode.NOT_CONFIGURED, "No connection profile is configured."
                )
            profile = await session.get(ConnectionProfileRecord, conversation.connection_profile_id)
            if profile is None:
                raise Prompt2InsightError(
                    ErrorCode.NOT_CONFIGURED, "Connection profile is unavailable."
                )
            catalog = await session.scalar(
                select(CatalogRevisionRecord)
                .where(CatalogRevisionRecord.connection_profile_id == profile.id)
                .order_by(CatalogRevisionRecord.created_at.desc())
            )
            snapshot = await session.scalar(
                select(SchemaSnapshotRecord)
                .where(SchemaSnapshotRecord.connection_profile_id == profile.id)
                .order_by(SchemaSnapshotRecord.created_at.desc(), SchemaSnapshotRecord.id.desc())
            )
            if catalog is None or snapshot is None:
                raise Prompt2InsightError(
                    ErrorCode.NOT_CONFIGURED, "Catalog or schema snapshot is unavailable."
                )
            if catalog.schema_snapshot_id != snapshot.id:
                raise Prompt2InsightError(
                    ErrorCode.CATALOG_STALE,
                    "The semantic catalog was validated against a different schema snapshot.",
                )
            schema_snapshot = SchemaSnapshot.model_validate(snapshot.snapshot)
            if profile.dialect != schema_snapshot.dialect.value:
                raise Prompt2InsightError(
                    ErrorCode.SCHEMA_CHANGED, "Schema dialect no longer matches profile."
                )
            return PlanningContext(
                dialect=schema_snapshot.dialect,
                catalog=AnalyticsCatalog.model_validate(catalog.content),
                schema_snapshot=schema_snapshot,
                catalog_revision_id=catalog.id,
                schema_snapshot_id=snapshot.id,
                connection_profile_id=profile.id,
                credential_reference=profile.credential_reference,
            )


class ConnectionProfileRepository:
    """Persistence operations for the existing connection-profile records."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _view(record: ConnectionProfileRecord, state: str) -> ConnectionProfileView:
        return ConnectionProfileView(
            id=record.id,
            name=record.name,
            dialect=SQLDialect(record.dialect),
            host=record.host,
            port=record.port,
            database_name=record.database_name,
            username=record.username,
            credential_reference=record.credential_reference,
            state=state,
            created_at=record.created_at,
        )

    async def list(self) -> list[ConnectionProfileView]:
        async with self._session_factory() as session:
            records = list((await session.scalars(select(ConnectionProfileRecord))).all())
            output: list[ConnectionProfileView] = []
            for record in records:
                snapshot = await session.scalar(
                    select(SchemaSnapshotRecord.id)
                    .where(SchemaSnapshotRecord.connection_profile_id == record.id)
                    .order_by(
                        SchemaSnapshotRecord.created_at.desc(), SchemaSnapshotRecord.id.desc()
                    )
                )
                catalog = await session.scalar(
                    select(CatalogRevisionRecord)
                    .where(CatalogRevisionRecord.connection_profile_id == record.id)
                    .order_by(
                        CatalogRevisionRecord.created_at.desc(), CatalogRevisionRecord.id.desc()
                    )
                )
                state = (
                    "ready"
                    if snapshot and catalog and catalog.schema_snapshot_id == snapshot
                    else "stale"
                    if snapshot and catalog
                    else "catalog_needs_configuration"
                    if snapshot
                    else "draft"
                )
                output.append(self._view(record, state))
            return output

    async def create(self, profile: ConnectionProfileInput) -> ConnectionProfileView:
        async with self._session_factory() as session:
            record = ConnectionProfileRecord(**profile.model_dump(mode="json"))
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return self._view(record, "draft")

    async def save_snapshot(self, profile_id: UUID, snapshot: SchemaSnapshot) -> None:
        async with self._session_factory() as session:
            session.add(
                SchemaSnapshotRecord(
                    connection_profile_id=profile_id,
                    content_hash=snapshot.fingerprint(),
                    snapshot=snapshot.model_dump(mode="json"),
                )
            )
            await session.commit()

    async def get_input(self, profile_id: UUID) -> ConnectionProfileInput | None:
        async with self._session_factory() as session:
            record = await session.get(ConnectionProfileRecord, profile_id)
            if record is None:
                return None
            return ConnectionProfileInput(
                name=record.name,
                dialect=SQLDialect(record.dialect),
                host=record.host,
                port=record.port,
                database_name=record.database_name,
                username=record.username,
                credential_reference=record.credential_reference,
            )

    async def refresh_snapshot(
        self, profile_id: UUID, snapshot: SchemaSnapshot
    ) -> tuple[SchemaSnapshotRecord, bool]:
        """Append a changed snapshot, or retain the current immutable snapshot."""
        content_hash = snapshot.fingerprint()
        async with self._session_factory() as session:
            current = await session.scalar(
                select(SchemaSnapshotRecord)
                .where(SchemaSnapshotRecord.connection_profile_id == profile_id)
                .order_by(SchemaSnapshotRecord.created_at.desc(), SchemaSnapshotRecord.id.desc())
            )
            if current is not None and current.content_hash == content_hash:
                return current, False
            record = SchemaSnapshotRecord(
                connection_profile_id=profile_id,
                content_hash=content_hash,
                snapshot=snapshot.model_dump(mode="json"),
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record, True

    async def state_for_snapshot(self, profile_id: UUID, snapshot_id: UUID) -> str:
        async with self._session_factory() as session:
            catalog = await session.scalar(
                select(CatalogRevisionRecord)
                .where(CatalogRevisionRecord.connection_profile_id == profile_id)
                .order_by(CatalogRevisionRecord.created_at.desc(), CatalogRevisionRecord.id.desc())
            )
            if catalog is None:
                return "catalog_needs_configuration"
            return "ready" if catalog.schema_snapshot_id == snapshot_id else "stale"

    async def create_conversation(self, profile_id: UUID) -> UUID:
        from uuid import uuid4

        async with self._session_factory() as session:
            conversation = ConversationRecord(id=uuid4(), connection_profile_id=profile_id)
            session.add(conversation)
            await session.commit()
            return conversation.id

    async def get_schema_snapshot_record(
        self, profile_id: UUID
    ) -> SchemaSnapshotRecord | None:
        async with self._session_factory() as session:
            record = await session.scalar(
                select(SchemaSnapshotRecord)
                .where(SchemaSnapshotRecord.connection_profile_id == profile_id)
                .order_by(SchemaSnapshotRecord.created_at.desc(), SchemaSnapshotRecord.id.desc())
            )
            return record

    async def get_schema_snapshot(self, profile_id: UUID) -> SchemaSnapshot | None:
        record = await self.get_schema_snapshot_record(profile_id)
        return SchemaSnapshot.model_validate(record.snapshot) if record is not None else None

    async def get_catalog(
        self, profile_id: UUID
    ) -> tuple[AnalyticsCatalog, str, UUID | None] | None:
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CatalogRevisionRecord)
                .where(CatalogRevisionRecord.connection_profile_id == profile_id)
                .order_by(CatalogRevisionRecord.created_at.desc(), CatalogRevisionRecord.id.desc())
            )
            if record is None:
                return None
            return (
                AnalyticsCatalog.model_validate(record.content),
                record.content_hash,
                record.schema_snapshot_id,
            )

    async def publish_catalog(
        self, profile_id: UUID, catalog: AnalyticsCatalog, schema_snapshot_id: UUID
    ) -> str:
        content = catalog.model_dump(mode="json")
        content_hash = sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        async with self._session_factory() as session:
            current_snapshot_id = await session.scalar(
                select(SchemaSnapshotRecord.id)
                .where(SchemaSnapshotRecord.connection_profile_id == profile_id)
                .order_by(SchemaSnapshotRecord.created_at.desc(), SchemaSnapshotRecord.id.desc())
            )
            if current_snapshot_id != schema_snapshot_id:
                raise Prompt2InsightError(
                    ErrorCode.CATALOG_STALE,
                    "The schema changed before the catalog could be published.",
                )
            existing = await session.scalar(
                select(CatalogRevisionRecord).where(
                    CatalogRevisionRecord.connection_profile_id == profile_id,
                    CatalogRevisionRecord.content_hash == content_hash,
                    CatalogRevisionRecord.schema_snapshot_id == schema_snapshot_id,
                )
            )
            if existing is None:
                session.add(
                    CatalogRevisionRecord(
                        connection_profile_id=profile_id,
                        schema_snapshot_id=schema_snapshot_id,
                        content_hash=content_hash,
                        content=content,
                    )
                )
                await session.commit()
            return content_hash
