from datetime import date

import pytest

from app.application.analytics.execute_query_plan import QueryPlanExecutor
from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import QueryPlan
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
from app.infrastructure.sql.validator import SQLValidator


def snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        dialect=SQLDialect.POSTGRES,
        database_name="analytics",
        server_version="16",
        capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
        tables=[
            TableMetadata(
                schema_name="analytics",
                table_name="orders",
                table_type="table",
                columns=[
                    ColumnMetadata(name="id", data_type="integer", nullable=False),
                    ColumnMetadata(name="customer_id", data_type="integer", nullable=False),
                    ColumnMetadata(name="region", data_type="text", nullable=True),
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
    )


def sales_snapshot() -> SchemaSnapshot:
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
                    ColumnMetadata(name="sales", data_type="numeric", nullable=False),
                    ColumnMetadata(name="order_id", data_type="text", nullable=False),
                    ColumnMetadata(name="order_date", data_type="timestamp", nullable=False),
                    ColumnMetadata(name="product_name", data_type="text", nullable=False),
                    ColumnMetadata(name="city", data_type="text", nullable=False),
                    ColumnMetadata(name="customer_name", data_type="text", nullable=False),
                ],
            )
        ],
    )


class Connector(SQLDatabaseConnector):
    dialect = SQLDialect.POSTGRES

    def __init__(self, current: SchemaSnapshot, *, estimated_cost: float = 1) -> None:
        self.current = current
        self.estimated_cost = estimated_cost
        self.explained = False
        self.executed = False
        self.explained_sql: str | None = None
        self.executed_sql: str | None = None
        self.explained_parameters: dict[str, object] | None = None
        self.executed_parameters: dict[str, object] | None = None
        self.prepared_query: PreparedQuery | None = None

    async def get_schema_snapshot(self) -> SchemaSnapshot:
        return self.current

    async def test_connection(self) -> None:
        return None

    async def explain(self, query: PreparedQuery) -> ExplainResult:
        self.explained = True
        self.explained_sql = query.sql
        self.explained_parameters = query.parameters
        self.prepared_query = query
        return ExplainResult(raw_plan={}, estimated_rows=1, estimated_cost=self.estimated_cost)

    async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
        self.executed = True
        self.executed_sql = query.sql
        self.executed_parameters = query.parameters
        return QueryResult(
            columns=["value"], rows=[], row_count=0, truncated=False, duration_ms=1
        )

    async def close(self) -> None:
        return None


def plan(
    sql: str,
    parameters: list[dict[str, object]] | None = None,
    *,
    metric_ids: list[str] | None = None,
    dimension_ids: list[str] | None = None,
) -> QueryPlan:
    return QueryPlan(
        status="ready",
        response_language="en",
        database_dialect=SQLDialect.POSTGRES,
        interpretation="x",
        metric_ids=metric_ids if metric_ids is not None else ["revenue"],
        dimension_ids=dimension_ids if dimension_ids is not None else ["region"],
        sql=sql,
        parameters=parameters or [],
    )


SAFE_SQL = (
    "SELECT analytics.orders.region, SUM(analytics.order_items.net_amount) "
    "FROM analytics.orders JOIN analytics.order_items "
    "ON analytics.orders.id = analytics.order_items.order_id "
    "GROUP BY analytics.orders.region"
)


async def test_schema_drift_stops_before_explain_and_execution() -> None:
    planned = snapshot()
    changed = planned.model_copy(deep=True)
    changed.tables[0].columns.append(
        ColumnMetadata(name="new_column", data_type="text", nullable=True)
    )
    connector = Connector(changed)

    with pytest.raises(Prompt2InsightError) as captured:
        await QueryPlanExecutor().execute(
            plan=plan(SAFE_SQL),
            schema_snapshot=planned,
            connector=connector,
            settings=Settings(),
        )

    assert captured.value.code is ErrorCode.SCHEMA_CHANGED
    assert not connector.explained and not connector.executed


async def test_valid_join_needs_no_semantic_contract() -> None:
    current = snapshot()
    connector = Connector(current)

    await QueryPlanExecutor().execute(
        plan=plan(SAFE_SQL),
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )

    assert connector.explained and connector.executed


async def test_scalar_parameters_reach_explain_and_execution_as_bindings() -> None:
    current = snapshot()
    connector = Connector(current)
    filtered_sql = SAFE_SQL.replace(
        " GROUP BY", " WHERE analytics.orders.region = :region GROUP BY"
    )

    await QueryPlanExecutor().execute(
        plan=plan(filtered_sql, [{"name": "region", "type": "string", "value": "West"}]),
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )

    assert connector.explained_parameters == {"region": "West"}
    assert connector.executed_parameters == {"region": "West"}


async def test_date_parameters_are_converted_before_explain_and_execution() -> None:
    current = sales_snapshot()
    connector = Connector(current)
    sql = (
        "SELECT analytics.sales.product_name, SUM(analytics.sales.sales) AS total_sales "
        "FROM analytics.sales WHERE analytics.sales.order_date >= :start_date "
        "AND analytics.sales.order_date < :end_date "
        "GROUP BY analytics.sales.product_name ORDER BY total_sales DESC LIMIT 5"
    )

    await QueryPlanExecutor().execute(
        plan=plan(
            sql,
            [
                {"name": "start_date", "type": "date", "value": "2015-01-01"},
                {"name": "end_date", "type": "date", "value": "2017-01-01"},
            ],
            metric_ids=["revenue"],
            dimension_ids=["product_name"],
        ),
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )

    assert connector.explained_parameters == {
        "start_date": date(2015, 1, 1),
        "end_date": date(2017, 1, 1),
    }
    assert connector.executed_parameters == connector.explained_parameters


async def test_invalid_typed_parameter_fails_before_database_execution() -> None:
    current = sales_snapshot()
    connector = Connector(current)

    with pytest.raises(Prompt2InsightError) as captured:
        await QueryPlanExecutor().execute(
            plan=plan(
                "SELECT analytics.sales.sales FROM analytics.sales "
                "WHERE analytics.sales.order_date >= :start_date",
                [{"name": "start_date", "type": "date", "value": "not-a-date"}],
                metric_ids=[],
                dimension_ids=[],
            ),
            schema_snapshot=current,
            connector=connector,
            settings=Settings(),
        )

    assert captured.value.code is ErrorCode.INVALID_QUERY_PARAMETER
    assert not connector.explained and not connector.executed


@pytest.mark.parametrize(
    ("sql", "metric_ids", "dimension_ids"),
    [
        (
            "SELECT SUM(analytics.sales.sales) / "
            "NULLIF(COUNT(DISTINCT analytics.sales.order_id), 0) AS average_order_total "
            "FROM analytics.sales",
            ["average_order_total"],
            [],
        ),
        (
            "SELECT AVG(analytics.sales.sales) AS average_sale FROM analytics.sales",
            ["revenue"],
            [],
        ),
        (
            "SELECT analytics.sales.product_name, SUM(analytics.sales.sales) AS total_sales "
            "FROM analytics.sales GROUP BY analytics.sales.product_name",
            ["revenue"],
            ["product_name"],
        ),
    ],
)
async def test_derived_canonical_mismatch_and_arbitrary_grouping_are_allowed(
    sql: str, metric_ids: list[str], dimension_ids: list[str]
) -> None:
    current = sales_snapshot()
    connector = Connector(current)

    await QueryPlanExecutor().execute(
        plan=plan(sql, metric_ids=metric_ids, dimension_ids=dimension_ids),
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )

    assert connector.explained and connector.executed


async def test_small_groups_receive_no_automatic_privacy_having_clause() -> None:
    current = sales_snapshot()
    connector = Connector(current)
    sql = (
        "SELECT analytics.sales.city, SUM(analytics.sales.sales) AS total_sales "
        "FROM analytics.sales GROUP BY analytics.sales.city"
    )

    execution = await QueryPlanExecutor().execute(
        plan=plan(sql, dimension_ids=["city"]),
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )

    assert "HAVING" not in execution.validated.normalized_sql.upper()
    assert connector.explained_sql == execution.validated.normalized_sql
    assert connector.executed_sql == execution.validated.normalized_sql


async def test_approved_column_is_queryable_regardless_of_old_sensitive_classification() -> None:
    current = sales_snapshot()
    connector = Connector(current)

    await QueryPlanExecutor().execute(
        plan=plan(
            "SELECT analytics.sales.customer_name FROM analytics.sales",
            metric_ids=[],
            dimension_ids=["customer_name"],
        ),
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )

    assert connector.executed


def test_schema_qualified_sources_are_exact_and_ctes_are_excluded() -> None:
    current = snapshot()
    policy = QueryPlanExecutor._policy(current, Settings())
    validator = SQLValidator()

    accepted = validator.validate(
        sql=(
            "WITH selected_orders AS (SELECT analytics.orders.id FROM analytics.orders AS o) "
            "SELECT id FROM selected_orders"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=policy,
    )
    assert accepted.referenced_tables == frozenset({"analytics.orders"})

    for source in ("private.orders", "pg_catalog.pg_tables", "information_schema.tables"):
        with pytest.raises(Prompt2InsightError) as captured:
            validator.validate(
                sql=f"SELECT id FROM {source}",
                dialect=SQLDialect.POSTGRES,
                policy=policy,
            )
        assert captured.value.code is ErrorCode.UNAUTHORIZED_TABLE


def test_policy_uses_all_tables_and_columns_from_approved_snapshot() -> None:
    policy = QueryPlanExecutor._policy(snapshot(), Settings())

    assert policy.allowed_tables == frozenset(
        {"analytics.orders", "analytics.order_items"}
    )
    assert "analytics.orders.customer_id" in policy.allowed_columns


async def test_cost_guard_runs_before_execution() -> None:
    current = snapshot()
    connector = Connector(current, estimated_cost=200_000)

    with pytest.raises(Prompt2InsightError) as captured:
        await QueryPlanExecutor().execute(
            plan=plan(SAFE_SQL),
            schema_snapshot=current,
            connector=connector,
            settings=Settings(max_query_cost=100_000),
        )

    assert captured.value.code is ErrorCode.QUERY_TOO_EXPENSIVE
    assert connector.explained and not connector.executed


async def test_row_timeout_and_lock_bounds_reach_read_only_connector() -> None:
    current = snapshot()
    connector = Connector(current)
    settings = Settings(max_output_rows=2, query_timeout_ms=8_000, lock_timeout_ms=2_000)

    execution = await QueryPlanExecutor().execute(
        plan=plan(SAFE_SQL),
        schema_snapshot=current,
        connector=connector,
        settings=settings,
    )

    assert "LIMIT 2" in execution.validated.normalized_sql
    assert connector.prepared_query is not None
    assert connector.prepared_query.maximum_rows == 2
    assert connector.prepared_query.timeout_ms == 8_000
    assert connector.prepared_query.lock_timeout_ms == 2_000
