from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from app.application.analytics.execute_query_plan import QueryPlanExecutor
from app.application.analytics.run_analytics_request import (
    AnalyticsRequestService,
    PlanningContext,
)
from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    AnswerOutput,
    ChartSpecification,
    ModelExecutionMetadata,
    QueryPlan,
)
from app.domain.databases.connector import SQLDatabaseConnector
from app.domain.databases.models import (
    ColumnMetadata,
    DatabaseCapabilities,
    ExplainResult,
    PreparedQuery,
    QueryResult,
    SchemaSnapshot,
    SQLDialect,
    TableMetadata,
)
from app.infrastructure.ai.litellm_gateway import GenerationResult, ModelGroup
from app.infrastructure.catalogs.models import AnalyticsCatalog


def snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        dialect=SQLDialect.POSTGRES,
        database_name="analytics",
        server_version="16",
        capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
        tables=[
            TableMetadata(
                schema_name="analytics",
                table_name="sales",
                table_type="table",
                columns=[
                    ColumnMetadata(name="city", data_type="text", nullable=False),
                    ColumnMetadata(name="sales", data_type="numeric", nullable=False),
                ],
            )
        ],
    )


def plan(sql: str = "SELECT analytics.sales.sales FROM analytics.sales") -> QueryPlan:
    return QueryPlan(
        status="ready",
        response_language="en",
        database_dialect=SQLDialect.POSTGRES,
        interpretation="sales",
        metric_ids=["revenue"],
        sql=sql,
    )


class Repository:
    def __init__(self, context: PlanningContext) -> None:
        self.context = context
        self.responses: dict[UUID, AnalyticsResponse] = {}

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        return self.responses.get(request_id)

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
        self.responses[request.request_id] = response
        return response


class Planner:
    def __init__(self, original: QueryPlan, repaired: QueryPlan | None = None) -> None:
        self.original = original
        self.repaired = repaired or plan(
            "SELECT SUM(analytics.sales.sales) AS total_sales FROM analytics.sales"
        )
        self.repair_calls: list[dict[str, object]] = []

    async def plan(self, **kwargs: object) -> GenerationResult[QueryPlan]:
        return GenerationResult(
            output=self.original,
            metadata=ModelExecutionMetadata(generation_stage="planner"),
        )

    async def repair(self, **kwargs: object) -> GenerationResult[QueryPlan]:
        self.repair_calls.append(kwargs)
        return GenerationResult(
            output=self.repaired,
            metadata=ModelExecutionMetadata(generation_stage="sql_repair"),
        )


class Answerer:
    def __init__(self, outputs: Sequence[AnswerOutput]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    async def generate(self, **kwargs: object) -> GenerationResult[AnswerOutput]:
        self.calls.append(kwargs)
        return GenerationResult(
            output=self.outputs.pop(0),
            metadata=ModelExecutionMetadata(
                generation_stage=str(kwargs.get("generation_stage"))
            ),
        )


class Connector(SQLDatabaseConnector):
    dialect = SQLDialect.POSTGRES

    def __init__(
        self, schema: SchemaSnapshot, outcomes: Sequence[QueryResult | Prompt2InsightError]
    ) -> None:
        self.schema = schema
        self.outcomes = list(outcomes)
        self.explained: list[PreparedQuery] = []
        self.executed: list[PreparedQuery] = []
        self.closed = False

    async def test_connection(self) -> None:
        return None

    async def get_schema_snapshot(self) -> SchemaSnapshot:
        return self.schema

    async def explain(self, query: PreparedQuery) -> ExplainResult:
        self.explained.append(query)
        return ExplainResult(raw_plan={}, estimated_cost=1, estimated_rows=1)

    async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
        self.executed.append(query)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Prompt2InsightError):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True


class Resolver:
    def __init__(self, connector: Connector) -> None:
        self.connector = connector

    async def connect(self, context: PlanningContext) -> SQLDatabaseConnector:
        return self.connector


def query_result(rows: list[list[object]] | None = None) -> QueryResult:
    result_rows = [[10]] if rows is None else rows
    return QueryResult(
        columns=["total_sales"],
        rows=result_rows,
        row_count=len(result_rows),
        truncated=False,
        duration_ms=1,
    )


def service(
    connector: Connector,
    planner: Planner,
    answerer: Answerer | None,
) -> AnalyticsRequestService:
    schema = connector.schema
    context = PlanningContext(
        dialect=SQLDialect.POSTGRES,
        catalog=AnalyticsCatalog(catalog_version="test", metrics={}, dimensions={}),
        schema_snapshot=schema,
        catalog_revision_id=uuid4(),
        schema_snapshot_id=uuid4(),
    )
    repository = Repository(context)
    return AnalyticsRequestService(
        mock_mode=False,
        repository=repository,
        planning_context_store=repository,
        planner=planner,
        planner_model_group=ModelGroup("planner", "primary", "fallback", QueryPlan),
        answerer=answerer,
        answer_model_group=(
            ModelGroup("answer", "primary", "fallback", AnswerOutput)
            if answerer is not None
            else None
        ),
        query_executor=QueryPlanExecutor(),
        connector_resolver=Resolver(connector),
        settings=Settings(mock_mode=False),
    )


async def run(
    service_under_test: AnalyticsRequestService, question: str = "Show total sales"
) -> AnalyticsResponse:
    return await service_under_test.run(
        conversation_id=uuid4(),
        request=AnalyticsRequest(request_id=uuid4(), question=question),
    )


async def test_recoverable_execution_failure_gets_one_fully_revalidated_repair() -> None:
    schema = snapshot()
    connector = Connector(
        schema,
        [
            Prompt2InsightError(
                ErrorCode.QUERY_EXECUTION_FAILED,
                "The database could not execute the query.",
                safe_detail="operator does not exist: numeric + text",
            ),
            query_result(),
        ],
    )
    planner = Planner(plan())

    response = await run(
        service(connector, planner, Answerer([AnswerOutput(answer="Total sales were 10.")]))
    )

    assert response.status is AnalyticsStatus.SUCCESS
    assert len(planner.repair_calls) == 1
    assert planner.repair_calls[0]["database_error"] == "operator does not exist: numeric + text"
    assert len(connector.explained) == 2
    assert len(connector.executed) == 2
    assert response.sql == connector.explained[1].sql == connector.executed[1].sql
    assert "LIMIT 1000" in (response.sql or "")
    assert response.query_plan == planner.repaired
    assert response.model_metadata is not None
    assert response.model_metadata.generation_stage == "sql_repair"
    assert any("fully revalidated" in warning for warning in response.warnings)


async def test_repaired_unsafe_sql_is_rejected_before_second_explain() -> None:
    schema = snapshot()
    connector = Connector(
        schema,
        [Prompt2InsightError(ErrorCode.QUERY_EXECUTION_FAILED, "bad query")],
    )
    planner = Planner(plan(), plan("DELETE FROM analytics.sales"))

    response = await run(service(connector, planner, None))

    assert response.status is AnalyticsStatus.FAILED
    assert response.error_code is ErrorCode.SQL_POLICY_REJECTED
    assert len(planner.repair_calls) == 1
    assert len(connector.explained) == 1
    assert len(connector.executed) == 1


async def test_second_query_execution_failure_is_final_and_does_not_loop() -> None:
    schema = snapshot()
    connector = Connector(
        schema,
        [
            Prompt2InsightError(ErrorCode.QUERY_EXECUTION_FAILED, "first"),
            Prompt2InsightError(ErrorCode.QUERY_EXECUTION_FAILED, "second"),
        ],
    )
    planner = Planner(plan())

    response = await run(service(connector, planner, None))

    assert response.status is AnalyticsStatus.FAILED
    assert response.error_code is ErrorCode.QUERY_EXECUTION_FAILED
    assert len(planner.repair_calls) == 1
    assert len(connector.explained) == 2
    assert len(connector.executed) == 2


@pytest.mark.parametrize(
    "error_code",
    [
        ErrorCode.DATABASE_UNAVAILABLE,
        ErrorCode.AUTHENTICATION_FAILED,
        ErrorCode.QUERY_TIMEOUT,
        ErrorCode.LOCK_TIMEOUT,
        ErrorCode.QUERY_TOO_EXPENSIVE,
        ErrorCode.SQL_POLICY_REJECTED,
    ],
)
async def test_non_recoverable_execution_errors_do_not_trigger_sql_repair(
    error_code: ErrorCode,
) -> None:
    schema = snapshot()
    connector = Connector(schema, [Prompt2InsightError(error_code, "hard failure")])
    planner = Planner(plan())

    response = await run(service(connector, planner, None))

    assert response.status is AnalyticsStatus.FAILED
    assert response.error_code is error_code
    assert planner.repair_calls == []


async def test_invalid_answer_is_regenerated_once_and_valid_retry_is_used() -> None:
    schema = snapshot()
    connector = Connector(schema, [query_result()])
    answerer = Answerer(
        [
            AnswerOutput(answer="Total sales were 999."),
            AnswerOutput(answer="Total sales were 10."),
        ]
    )

    response = await run(service(connector, Planner(plan()), answerer))

    assert response.status is AnalyticsStatus.SUCCESS
    assert response.answer == "Total sales were 10."
    assert len(answerer.calls) == 2
    assert answerer.calls[1]["generation_stage"] == "answer_regeneration"
    assert "Validation feedback" in str(answerer.calls[1]["user_prompt"])
    assert any("regenerated once" in warning for warning in response.warnings)


async def test_second_invalid_answer_uses_deterministic_fallback_and_stays_successful() -> None:
    schema = snapshot()
    connector = Connector(schema, [query_result()])
    answerer = Answerer(
        [
            AnswerOutput(answer="Total sales were 999."),
            AnswerOutput(answer="Total sales were 888."),
        ]
    )

    response = await run(service(connector, Planner(plan()), answerer))

    assert response.status is AnalyticsStatus.SUCCESS
    assert response.error_code is None
    assert response.answer == "Query completed successfully. 1 row returned."
    assert response.table is not None and response.table.rows == [[10]]
    assert response.sql is not None
    assert response.query_plan is not None
    assert len(answerer.calls) == 2
    assert any("deterministic result summary" in warning for warning in response.warnings)


async def test_invalid_chart_is_omitted_without_failing_the_query() -> None:
    schema = snapshot()
    connector = Connector(schema, [query_result()])
    answerer = Answerer(
        [
            AnswerOutput(
                answer="Total sales were 10.",
                chart=ChartSpecification(
                    chart_type="bar",
                    x_column="invented_column",
                    y_columns=["total_sales"],
                    title="Sales",
                ),
            )
        ]
    )

    response = await run(service(connector, Planner(plan()), answerer))

    assert response.status is AnalyticsStatus.SUCCESS
    assert response.answer == "Total sales were 10."
    assert response.chart is None
    assert len(answerer.calls) == 1
    assert any("invalid chart was omitted" in warning for warning in response.warnings)


async def test_valid_chart_is_retained() -> None:
    schema = snapshot()
    connector = Connector(schema, [query_result()])
    chart = ChartSpecification(
        chart_type="bar",
        x_column="total_sales",
        y_columns=["total_sales"],
        title="Sales",
    )
    answerer = Answerer([AnswerOutput(answer="Total sales were 10.", chart=chart)])

    response = await run(service(connector, Planner(plan()), answerer))

    assert response.status is AnalyticsStatus.SUCCESS
    assert response.chart == chart


async def test_contextual_year_does_not_turn_executed_query_into_failure() -> None:
    schema = snapshot()
    connector = Connector(schema, [query_result()])
    answerer = Answerer([AnswerOutput(answer="In 2016, total sales were 10.")])

    response = await run(
        service(connector, Planner(plan()), answerer),
        question="Show total sales in 2016",
    )

    assert response.status is AnalyticsStatus.SUCCESS
    assert response.answer == "In 2016, total sales were 10."
    assert len(answerer.calls) == 1


async def test_empty_result_skips_answer_generation_and_preserves_sql() -> None:
    schema = snapshot()
    connector = Connector(schema, [query_result([])])
    answerer = Answerer([])

    response = await run(service(connector, Planner(plan()), answerer))

    assert response.status is AnalyticsStatus.EMPTY_RESULT
    assert response.answer == "No matching rows were returned."
    assert response.table is not None and response.table.rows == []
    assert response.sql is not None
    assert answerer.calls == []
