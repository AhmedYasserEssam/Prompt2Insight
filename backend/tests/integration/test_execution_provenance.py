import os
from collections.abc import AsyncIterator
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.analytics.run_analytics_request import PlanningContext
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    ModelExecutionMetadata,
    QueryPlan,
    ResultTable,
)
from app.domain.databases.models import DatabaseCapabilities, SchemaSnapshot, SQLDialect
from app.infrastructure.catalogs.models import AnalyticsCatalog
from app.persistence.models import (
    AnalyticalRequestRecord,
    CatalogRevisionRecord,
    ConnectionProfileRecord,
    ConversationRecord,
    QueryExecutionRecord,
    SchemaSnapshotRecord,
)
from app.persistence.repositories import AnalyticsRequestRepository

pytestmark = pytest.mark.skipif(
    os.getenv("P2I_RUN_PERSISTENCE_INTEGRATION") != "1",
    reason=(
        "set P2I_RUN_PERSISTENCE_INTEGRATION=1 and P2I_APP_DATABASE_URL "
        "to run persistence tests"
    ),
)


@pytest.fixture
async def repository() -> AsyncIterator[AnalyticsRequestRepository]:
    database_url = os.environ["P2I_APP_DATABASE_URL"]
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield AnalyticsRequestRepository(factory)
    finally:
        await engine.dispose()


async def _parents(repository: AnalyticsRequestRepository) -> tuple[PlanningContext, UUID]:
    factory = repository._session_factory
    profile_id, conversation_id, catalog_id, snapshot_id = uuid4(), uuid4(), uuid4(), uuid4()
    snapshot = SchemaSnapshot(
        dialect=SQLDialect.POSTGRES,
        database_name="analytics",
        server_version="16",
        tables=[],
        capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
    )
    catalog = AnalyticsCatalog.model_validate(
        {
            "catalog_version": "test",
            "metrics": {},
            "dimensions": {},
            "join_contracts": [],
            "column_policies": {},
            "privacy": {"privacy_unit": "analytics.orders.id", "minimum_group_size": 1},
        }
    )
    async with factory() as session:
        session.add(
            ConnectionProfileRecord(
                id=profile_id,
                name=str(profile_id),
                dialect="postgres",
                host="db",
                port=5432,
                database_name="analytics",
                username="analytics",
                credential_reference="P2I_TEST_DB_URL",
            )
        )
        await session.flush()
        session.add(ConversationRecord(id=conversation_id, connection_profile_id=profile_id))
        session.add(
            CatalogRevisionRecord(
                id=catalog_id,
                connection_profile_id=profile_id,
                schema_snapshot_id=snapshot_id,
                content_hash=sha256(str(catalog_id).encode()).hexdigest(),
                content=catalog.model_dump(mode="json"),
            )
        )
        session.add(
            SchemaSnapshotRecord(
                id=snapshot_id,
                connection_profile_id=profile_id,
                content_hash=sha256(str(snapshot_id).encode()).hexdigest(),
                snapshot=snapshot.model_dump(mode="json"),
            )
        )
        await session.commit()
    return PlanningContext(
        dialect=SQLDialect.POSTGRES,
        catalog=catalog,
        schema_snapshot=snapshot,
        catalog_revision_id=catalog_id,
        schema_snapshot_id=snapshot_id,
        connection_profile_id=profile_id,
        credential_reference="P2I_TEST_DB_URL",
    ), conversation_id


async def test_planning_context_rejects_catalog_linked_to_old_snapshot(
    repository: AnalyticsRequestRepository,
) -> None:
    _, conversation_id = await _parents(repository)
    factory = repository._session_factory
    async with factory() as session:
        conversation = await session.get(ConversationRecord, conversation_id)
        assert conversation is not None
        session.add(
            SchemaSnapshotRecord(
                connection_profile_id=conversation.connection_profile_id,
                content_hash=sha256(str(uuid4()).encode()).hexdigest(),
                snapshot=SchemaSnapshot(
                    dialect=SQLDialect.POSTGRES,
                    database_name="analytics",
                    server_version="16",
                    tables=[],
                    capabilities=DatabaseCapabilities(
                        dialect=SQLDialect.POSTGRES, server_version="16"
                    ),
                ).model_dump(mode="json"),
            )
        )
        await session.commit()

    with pytest.raises(Prompt2InsightError) as captured:
        await repository.get_planning_context(conversation_id)
    assert captured.value.code is ErrorCode.CATALOG_STALE


@pytest.mark.parametrize(
    ("status", "rows", "truncated", "error_code", "fallback"),
    [
        (AnalyticsStatus.SUCCESS, [["DO_NOT_PERSIST_PROTECTED_RESULT"]], False, None, False),
        (AnalyticsStatus.EMPTY_RESULT, [], False, None, False),
        (AnalyticsStatus.SUCCESS, [["DO_NOT_PERSIST_PROTECTED_RESULT"]], True, None, True),
        (AnalyticsStatus.FAILED, [], False, ErrorCode.QUERY_TIMEOUT, False),
    ],
)
async def test_repository_persists_execution_provenance(
    repository: AnalyticsRequestRepository,
    status: AnalyticsStatus,
    rows: list[list[str]],
    truncated: bool,
    error_code: ErrorCode | None,
    fallback: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = [
        "TEST_DATABASE_SECRET_DO_NOT_PERSIST",
        "TEST_GROQ_SECRET_DO_NOT_PERSIST",
        "TEST_LITELLM_SECRET_DO_NOT_PERSIST",
    ]
    monkeypatch.setenv("P2I_TEST_DB_URL", secrets[0])
    monkeypatch.setenv("GROQ_API_KEY", secrets[1])
    monkeypatch.setenv("LITELLM_MASTER_KEY", secrets[2])
    context, conversation_id = await _parents(repository)
    request = AnalyticsRequest(request_id=uuid4(), question="revenue")
    sql = "SELECT region FROM analytics.orders LIMIT 1000"
    plan = QueryPlan(
        status="ready",
        response_language="en",
        database_dialect=SQLDialect.POSTGRES,
        interpretation="revenue",
        sql=sql,
    )
    metadata = ModelExecutionMetadata(
        provider="groq",
        model="fallback-model" if fallback else "primary-model",
        actual_model="actual",
        latency_ms=17,
        fallback_used=fallback,
        fallback_reason="primary_unavailable" if fallback else None,
    )
    response = AnalyticsResponse(
        status=status,
        request_id=request.request_id,
        language="en",
        sql=sql,
        query_plan=plan,
        table=ResultTable(columns=["region"], rows=rows),
        warnings=["Result rows were truncated to the configured limit."] if truncated else [],
        error_code=error_code,
        model_metadata=metadata,
    )
    await repository.save(
        conversation_id=conversation_id,
        request=request,
        response=response,
        planning_context=context,
    )

    async with repository._session_factory() as session:
        execution = await session.scalar(
            select(QueryExecutionRecord).where(
                QueryExecutionRecord.request_id == request.request_id
            )
        )
        stored_request = await session.get(AnalyticalRequestRecord, request.request_id)
        stored_conversation = await session.get(ConversationRecord, conversation_id)
    assert execution is not None and stored_request is not None and stored_conversation is not None
    assert execution.status == status.value
    assert execution.validated_sql == sql
    assert execution.sql_hash == sha256(sql.encode()).hexdigest()
    assert execution.dialect == SQLDialect.POSTGRES.value
    assert execution.row_count == len(rows)
    assert execution.truncated is truncated
    assert execution.error_code == (error_code.value if error_code else None)
    assert execution.latency_ms == 17
    assert execution.model_metadata["provider"] == "groq"
    assert execution.model_metadata["model"] == metadata.model
    assert execution.plan_metadata["fallback_used"] is fallback
    assert execution.plan_metadata["fallback_reason"] == metadata.fallback_reason
    assert stored_request.catalog_revision_id == context.catalog_revision_id
    assert stored_request.schema_snapshot_id == context.schema_snapshot_id
    assert stored_conversation.connection_profile_id == context.connection_profile_id
    persisted = (
        str(execution.model_metadata) + str(execution.plan_metadata) + str(execution.validated_sql)
    )
    assert all(secret not in persisted for secret in secrets)
    assert "DO_NOT_PERSIST_PROTECTED_RESULT" not in persisted


async def test_sql_hash_changes_with_validated_sql(repository: AnalyticsRequestRepository) -> None:
    context, conversation_id = await _parents(repository)
    hashes: list[str] = []
    for sql in ("SELECT 1", "SELECT 1", "SELECT 2"):
        request = AnalyticsRequest(request_id=uuid4(), question="x")
        response = AnalyticsResponse(
            status=AnalyticsStatus.SUCCESS, request_id=request.request_id, language="en", sql=sql
        )
        await repository.save(
            conversation_id=conversation_id,
            request=request,
            response=response,
            planning_context=context,
        )
        async with repository._session_factory() as session:
            execution = await session.scalar(
                select(QueryExecutionRecord).where(
                    QueryExecutionRecord.request_id == request.request_id
                )
            )
        assert execution is not None
        hashes.append(execution.sql_hash or "")
        assert execution.sql_hash == sha256(sql.encode()).hexdigest()
    assert hashes[0] == hashes[1]
    assert hashes[1] != hashes[2]
