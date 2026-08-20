import json
import os
from collections.abc import AsyncIterator
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.analytics.run_analytics_request import PlanningContext
from app.application.databases.configure_catalog import CatalogConfigurationService
from app.application.databases.setup_connection import ConnectionSetupService
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    ModelExecutionMetadata,
    QueryPlan,
    ResultTable,
)
from app.domain.databases.connection_profiles import ConnectionProfileInput
from app.domain.databases.models import (
    ColumnMetadata,
    DatabaseCapabilities,
    SchemaSnapshot,
    SQLDialect,
    TableMetadata,
)
from app.infrastructure.catalogs.models import AnalyticsCatalog
from app.persistence.models import (
    AnalyticalRequestRecord,
    CatalogRevisionRecord,
    ConnectionProfileRecord,
    ConversationRecord,
    QueryExecutionRecord,
    SchemaSnapshotRecord,
)
from app.persistence.repositories import AnalyticsRequestRepository, ConnectionProfileRepository

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
        database_name=str(profile_id),
        server_version="16",
        tables=[],
        capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
    )
    catalog = AnalyticsCatalog.model_validate(
        {
            "catalog_version": "test",
            "metrics": {},
            "dimensions": {},
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
        session.add(
            SchemaSnapshotRecord(
                id=snapshot_id,
                connection_profile_id=profile_id,
                content_hash=sha256(str(snapshot_id).encode()).hexdigest(),
                snapshot=snapshot.model_dump(mode="json"),
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
        parameters=[
            {"name": "start_date", "type": "date", "value": "2015-01-01"},
        ],
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
    assert execution.plan_metadata["parameters"] == [
        {"name": "start_date", "type": "date", "value": "2015-01-01"},
    ]
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


async def test_published_catalog_persists_and_reloads_canonical_references(
    repository: AnalyticsRequestRepository,
) -> None:
    profile_id, conversation_id, snapshot_id = uuid4(), uuid4(), uuid4()
    snapshot = SchemaSnapshot(
        dialect=SQLDialect.POSTGRES,
        database_name=str(profile_id),
        server_version="16",
        capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
        tables=[
            TableMetadata(
                schema_name="analytics",
                table_name="sales",
                table_type="table",
                columns=[
                    ColumnMetadata(name=name, data_type="text", nullable=False)
                    for name in (
                        "sales", "order_id", "order_date", "customer_id", "customer_name",
                        "postal_code", "category", "region",
                    )
                ],
            )
        ],
    )
    draft = AnalyticsCatalog.model_validate(
        {
            "catalog_version": "test",
            "metrics": {
                "revenue": {
                    "labels": {"en": "Revenue", "ar": "Revenue"},
                    "aliases": {"en": [], "ar": []},
                    "descriptions": {"en": "", "ar": ""},
                    "expressions": {
                        "postgres": "SUM(sales.sales)",
                        "mysql": "SUM(sales.sales)",
                    },
                }
            },
            "dimensions": {
                "month": {
                    "labels": {"en": "Month", "ar": "Month"},
                    "aliases": {"en": [], "ar": []},
                    "descriptions": {"en": "", "ar": ""},
                    "expressions": {
                        "postgres": "DATE_TRUNC('month', sales.order_date)",
                        "mysql": "DATE_TRUNC('month', sales.order_date)",
                    },
                }
            },
        }
    )
    factory = repository._session_factory
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
        session.add(ConversationRecord(id=conversation_id, connection_profile_id=profile_id))
        session.add(
            SchemaSnapshotRecord(
                id=snapshot_id,
                connection_profile_id=profile_id,
                content_hash=snapshot.fingerprint(),
                snapshot=snapshot.model_dump(mode="json"),
            )
        )
        await session.commit()

    configuration = CatalogConfigurationService(ConnectionProfileRepository(factory))
    published = await configuration.publish(profile_id, draft)
    canonical = published.catalog
    assert canonical is not None
    equivalent = canonical.model_copy(deep=True)
    await configuration.publish(profile_id, equivalent)

    async with factory() as session:
        records = list(
            (
                await session.scalars(
                    select(CatalogRevisionRecord).where(
                        CatalogRevisionRecord.connection_profile_id == profile_id
                    )
                )
            ).all()
        )
    assert len(records) == 1
    record = records[0]
    content = record.content
    assert content["metrics"]["revenue"]["expressions"]["postgres"] == (  # type: ignore[index]
        "SUM(analytics.sales.sales)"
    )
    assert content["dimensions"]["month"]["expressions"]["postgres"] == (  # type: ignore[index]
        "DATE_TRUNC('MONTH', analytics.sales.order_date)"
    )
    assert record.content_hash == sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    context = await repository.get_planning_context(conversation_id)
    assert context.catalog.metric_expression("revenue", SQLDialect.POSTGRES) == (
        "SUM(analytics.sales.sales)"
    )


async def test_schema_snapshot_persists_postgres_namespace(
    repository: AnalyticsRequestRepository,
) -> None:
    profile_id = uuid4()
    snapshot = SchemaSnapshot(
        dialect=SQLDialect.POSTGRES,
        database_name=str(profile_id),
        server_version="16",
        capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
        tables=[
            TableMetadata(
                schema_name="analytics",
                table_name="sales",
                table_type="table",
                columns=[ColumnMetadata(name="sales", data_type="numeric", nullable=False)],
            )
        ],
    )
    factory = repository._session_factory
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
        await session.commit()

    profiles = ConnectionProfileRepository(factory)
    await profiles.save_snapshot(profile_id, snapshot)
    record = await profiles.get_schema_snapshot_record(profile_id)

    assert record is not None
    assert record.snapshot["tables"][0]["schema_name"] == "analytics"  # type: ignore[index]
    assert SchemaSnapshot.model_validate(record.snapshot).tables[0].schema_name == "analytics"


async def test_refresh_schema_persists_current_postgres_namespace_and_preserves_history(
    repository: AnalyticsRequestRepository,
) -> None:
    if os.getenv("P2I_RUN_INTEGRATION") != "1":
        pytest.skip("set P2I_RUN_INTEGRATION=1 to run the live PostgreSQL refresh test")
    factory = repository._session_factory
    profiles = ConnectionProfileRepository(factory)
    profile = await profiles.create(
        ConnectionProfileInput(
            name=str(uuid4()),
            dialect=SQLDialect.POSTGRES,
            host="postgres-analytics",
            port=5432,
            database_name="analytics",
            username="analytics",
            credential_reference="P2I_POSTGRES_ANALYTICS_URL",
        )
    )
    old_snapshot = SchemaSnapshot(
        dialect=SQLDialect.POSTGRES,
        database_name="analytics",
        server_version="16",
        capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
        tables=[
            TableMetadata(schema_name=None, table_name="sales", table_type="table", columns=[])
        ],
    )
    await profiles.save_snapshot(profile.id, old_snapshot)
    old_record = await profiles.get_schema_snapshot_record(profile.id)
    assert old_record is not None
    old_snapshot_id = old_record.id
    old_snapshot_content = old_record.snapshot
    async with factory() as session:
        session.add(
            CatalogRevisionRecord(
                connection_profile_id=profile.id,
                schema_snapshot_id=old_snapshot_id,
                content_hash=sha256(str(uuid4()).encode()).hexdigest(),
                content={
                    "catalog_version": "test",
                    "metrics": {},
                    "dimensions": {},
                },
            )
        )
        await session.commit()

    result = await ConnectionSetupService(profiles).refresh_schema(profile.id)
    refreshed = await profiles.get_schema_snapshot_record(profile.id)

    assert result.schema_changed
    assert result.state == "stale"
    assert refreshed is not None and refreshed.id != old_snapshot_id
    assert any(
        table["schema_name"] == "analytics" and table["table_name"] == "sales"
        for table in refreshed.snapshot["tables"]  # type: ignore[index]
    )
    async with factory() as session:
        preserved_old = await session.get(SchemaSnapshotRecord, old_snapshot_id)
        catalog = await session.scalar(
            select(CatalogRevisionRecord).where(
                CatalogRevisionRecord.connection_profile_id == profile.id
            )
        )
    assert preserved_old is not None and preserved_old.snapshot == old_snapshot_content
    assert catalog is not None and catalog.schema_snapshot_id == old_snapshot_id
