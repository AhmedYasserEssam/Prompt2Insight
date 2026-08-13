from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.application.analytics.execute_query_plan import QueryPlanExecutor
from app.application.analytics.run_analytics_request import AnalyticsRequestService, PlanningContext
from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    ModelExecutionMetadata,
    QueryPlan,
)
from app.domain.databases.models import (
    ColumnMetadata,
    DatabaseCapabilities,
    ExplainResult,
    QueryResult,
    SchemaSnapshot,
    SQLDialect,
    TableMetadata,
)
from app.infrastructure.ai.litellm_gateway import GenerationResult, ModelGroup
from app.infrastructure.catalogs.loader import load_catalog


class InMemoryRequestRepository:
    def __init__(self) -> None:
        self.responses: dict[UUID, AnalyticsResponse] = {}
        self.saved_conversation_id: UUID | None = None

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        return self.responses.get(request_id)

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
    ) -> AnalyticsResponse:
        self.saved_conversation_id = conversation_id
        self.responses[request.request_id] = response
        return response


async def test_request_is_persisted_and_recovered_by_global_id() -> None:
    repository = InMemoryRequestRepository()
    service = AnalyticsRequestService(mock_mode=True, repository=repository)
    conversation_id = uuid4()
    request = AnalyticsRequest(request_id=uuid4(), question="Revenue by month")

    created = await service.run(conversation_id=conversation_id, request=request)
    recovered = await service.get(request.request_id)

    assert created.request_id == request.request_id
    assert recovered == created
    assert repository.saved_conversation_id == conversation_id
    assert created.model_metadata is None


class ProductionRepository(InMemoryRequestRepository):
    def __init__(self, context: PlanningContext) -> None:
        super().__init__()
        self.context = context
        self.saved_context: PlanningContext | None = None

    async def get_planning_context(self, conversation_id: UUID) -> PlanningContext:
        return self.context

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
        planning_context: PlanningContext | None = None,
    ) -> AnalyticsResponse:
        self.saved_context = planning_context
        return await super().save(
            conversation_id=conversation_id, request=request, response=response
        )


class PlannerStub:
    def __init__(self, result: GenerationResult[QueryPlan]) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def plan(self, **kwargs: object) -> GenerationResult[QueryPlan]:
        self.calls.append(kwargs)
        return self.result


class ConnectorStub:
    dialect = SQLDialect.MYSQL

    def __init__(self, snapshot: SchemaSnapshot) -> None:
        self._snapshot = snapshot

    async def get_schema_snapshot(self) -> SchemaSnapshot:
        return self._snapshot

    async def explain(self, query):
        return ExplainResult(raw_plan={}, estimated_cost=1, estimated_rows=1)

    async def execute_read_only(self, query):
        return QueryResult(
            columns=["region", "revenue"],
            rows=[["Cairo", 10]],
            row_count=1,
            truncated=False,
            duration_ms=1,
        )

    async def close(self):
        pass


class ConnectorResolverStub:
    def __init__(self, snapshot: SchemaSnapshot) -> None:
        self._snapshot = snapshot

    async def connect(self, context):
        return ConnectorStub(self._snapshot)


class UnauthorizedExecutor(QueryPlanExecutor):
    async def execute(self, **_: object):
        raise Prompt2InsightError(ErrorCode.UNAUTHORIZED_TABLE, "Unapproved tables: secret.sales.")


class UnauthorizedColumnExecutor(QueryPlanExecutor):
    async def execute(self, **_: object):
        raise Prompt2InsightError(
            ErrorCode.UNAUTHORIZED_COLUMN,
            "Sensitive columns are not queryable: analytics.sales.customer_id.",
        )


async def test_production_request_reaches_planner_and_persists_planning_context() -> None:
    catalog, _ = load_catalog(
        Path(__file__).parents[2] / "catalogs" / "analytics_catalog.example.yaml"
    )
    context = PlanningContext(
        dialect=SQLDialect.MYSQL,
        catalog=catalog,
        schema_snapshot=SchemaSnapshot(
            dialect=SQLDialect.MYSQL,
            database_name="analytics",
            server_version="8",
            tables=[
                TableMetadata(
                    schema_name="analytics",
                    table_name="orders",
                    table_type="table",
                    columns=[
                        ColumnMetadata(name="id", data_type="integer", nullable=False),
                        ColumnMetadata(name="region", data_type="text", nullable=True),
                        ColumnMetadata(name="customer_id", data_type="integer", nullable=False),
                    ],
                ),
                TableMetadata(
                    schema_name="analytics",
                    table_name="order_items",
                    table_type="table",
                    columns=[
                        ColumnMetadata(name="order_id", data_type="integer", nullable=False),
                        ColumnMetadata(name="net_amount", data_type="numeric", nullable=False),
                    ],
                ),
            ],
            capabilities=DatabaseCapabilities(dialect=SQLDialect.MYSQL, server_version="8"),
        ),
        catalog_revision_id=uuid4(),
        schema_snapshot_id=uuid4(),
    )
    plan = QueryPlan(
        status="ready",
        response_language="ar",
        database_dialect=SQLDialect.MYSQL,
        interpretation="الإيرادات حسب الشهر",
        metric_ids=["revenue"],
        dimension_ids=["region"],
        sql=(
            "SELECT analytics.orders.region, SUM(analytics.order_items.net_amount) AS revenue "
            "FROM analytics.orders JOIN analytics.order_items "
            "ON analytics.orders.id = analytics.order_items.order_id "
            "GROUP BY analytics.orders.region "
            "HAVING COUNT(DISTINCT analytics.orders.customer_id) >= 5"
        ),
    )
    planner = PlannerStub(
        GenerationResult(
            output=plan,
            metadata=ModelExecutionMetadata(
                provider="litellm", model="groq/qwen/qwen3.6-27b", generation_stage="planner"
            ),
        )
    )
    repository = ProductionRepository(context)
    service = AnalyticsRequestService(
        mock_mode=False,
        repository=repository,
        planning_context_store=repository,
        planner=planner,
        planner_model_group=ModelGroup("planner", "primary", "fallback", QueryPlan),
        query_executor=QueryPlanExecutor(),
        connector_resolver=ConnectorResolverStub(context.schema_snapshot),
        settings=Settings(mock_mode=False),
    )
    request = AnalyticsRequest(request_id=uuid4(), question="وريني revenue لكل شهر في 2025")

    response = await service.run(conversation_id=uuid4(), request=request)

    assert response.status is AnalyticsStatus.SUCCESS
    assert response.query_plan == plan
    assert response.model_metadata == planner.result.metadata
    assert repository.saved_context == context
    assert planner.calls[0]["dialect"] is SQLDialect.MYSQL
    assert planner.calls[0]["catalog"] == catalog


async def test_execution_failure_preserves_real_planner_metadata() -> None:
    catalog, _ = load_catalog(
        Path(__file__).parents[2] / "catalogs" / "analytics_catalog.example.yaml"
    )
    context = PlanningContext(
        dialect=SQLDialect.MYSQL,
        catalog=catalog,
        schema_snapshot=SchemaSnapshot(
            dialect=SQLDialect.MYSQL,
            database_name="analytics",
            server_version="8",
            tables=[],
            capabilities=DatabaseCapabilities(dialect=SQLDialect.MYSQL, server_version="8"),
        ),
        catalog_revision_id=uuid4(),
        schema_snapshot_id=uuid4(),
    )
    metadata = ModelExecutionMetadata(
        provider="litellm", model="groq/qwen", generation_stage="planner"
    )
    planner = PlannerStub(
        GenerationResult(
            output=QueryPlan(
                status="ready", response_language="en", database_dialect=SQLDialect.MYSQL,
                interpretation="sales", sql="SELECT 1",
            ),
            metadata=metadata,
        )
    )
    repository = ProductionRepository(context)
    service = AnalyticsRequestService(
        mock_mode=False,
        repository=repository,
        planning_context_store=repository,
        planner=planner,
        planner_model_group=ModelGroup("planner", "primary", "fallback", QueryPlan),
        query_executor=UnauthorizedExecutor(),
        connector_resolver=ConnectorResolverStub(context.schema_snapshot),
        settings=Settings(mock_mode=False),
    )

    response = await service.run(
        conversation_id=uuid4(), request=AnalyticsRequest(request_id=uuid4(), question="sales")
    )

    assert response.error_code is ErrorCode.UNAUTHORIZED_TABLE
    assert response.model_metadata == metadata


@pytest.mark.parametrize(
    ("executor", "code", "detail"),
    [
        (
            UnauthorizedColumnExecutor(),
            ErrorCode.UNAUTHORIZED_COLUMN,
            "Sensitive columns are not queryable: analytics.sales.customer_id.",
        ),
        (UnauthorizedExecutor(), ErrorCode.UNAUTHORIZED_TABLE, "Unapproved tables: secret.sales."),
    ],
)
async def test_policy_failure_logs_internal_detail_without_exposing_it(
    caplog: pytest.LogCaptureFixture,
    executor: QueryPlanExecutor,
    code: ErrorCode,
    detail: str,
) -> None:
    catalog, _ = load_catalog(
        Path(__file__).parents[2] / "catalogs" / "analytics_catalog.example.yaml"
    )
    context = PlanningContext(
        dialect=SQLDialect.MYSQL,
        catalog=catalog,
        schema_snapshot=SchemaSnapshot(
            dialect=SQLDialect.MYSQL,
            database_name="analytics",
            server_version="8",
            tables=[],
            capabilities=DatabaseCapabilities(dialect=SQLDialect.MYSQL, server_version="8"),
        ),
        catalog_revision_id=uuid4(),
        schema_snapshot_id=uuid4(),
    )
    request = AnalyticsRequest(request_id=uuid4(), question="اعرض إجمالي المبيعات لكل شهر")
    planner = PlannerStub(
        GenerationResult(
            output=QueryPlan(
                status="ready",
                response_language="ar",
                database_dialect=SQLDialect.MYSQL,
                interpretation="المبيعات الشهرية",
                sql="SELECT 1",
            ),
            metadata=ModelExecutionMetadata(provider="litellm", model="test"),
        )
    )
    repository = ProductionRepository(context)
    service = AnalyticsRequestService(
        mock_mode=False,
        repository=repository,
        planning_context_store=repository,
        planner=planner,
        planner_model_group=ModelGroup("planner", "primary", "fallback", QueryPlan),
        query_executor=executor,
        connector_resolver=ConnectorResolverStub(context.schema_snapshot),
        settings=Settings(mock_mode=False),
    )

    with caplog.at_level("WARNING"):
        response = await service.run(conversation_id=uuid4(), request=request)

    assert response.error_code is code
    assert detail not in response.model_dump_json()
    assert f"request_id={request.request_id}" in caplog.text
    assert f"error_code={code.value}" in caplog.text
    assert detail in caplog.text
