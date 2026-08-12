from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.analytics.run_analytics_request import PlanningContext
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnalyticsRequest, AnalyticsResponse
from app.domain.databases.models import SchemaSnapshot
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
                            "parameters": response.query_plan.parameters,
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
                        else {"mode": "mock"}
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
                .order_by(SchemaSnapshotRecord.created_at.desc())
            )
            if catalog is None or snapshot is None:
                raise Prompt2InsightError(
                    ErrorCode.NOT_CONFIGURED, "Catalog or schema snapshot is unavailable."
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
