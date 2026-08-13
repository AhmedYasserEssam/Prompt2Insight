from collections.abc import Callable
from pathlib import Path

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
from app.infrastructure.catalogs.loader import load_catalog
from app.infrastructure.catalogs.models import AnalyticsCatalog
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
                    ColumnMetadata(name=name, data_type="integer", nullable=False)
                    for name in ("id", "customer_id")
                ]
                + [ColumnMetadata(name="region", data_type="text", nullable=True)],
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


class Connector(SQLDatabaseConnector):
    dialect = SQLDialect.POSTGRES

    def __init__(self, current: SchemaSnapshot) -> None:
        self.current = current
        self.explained = False
        self.executed = False

    async def get_schema_snapshot(self) -> SchemaSnapshot:
        return self.current

    async def test_connection(self) -> None:
        return None

    async def explain(self, query: PreparedQuery) -> ExplainResult:
        self.explained = True
        return ExplainResult(raw_plan={}, estimated_rows=1, estimated_cost=1)

    async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
        self.executed = True
        return QueryResult(
            columns=["region", "revenue"], rows=[], row_count=0, truncated=False, duration_ms=1
        )

    async def close(self) -> None:
        return None


def plan(sql: str) -> QueryPlan:
    return QueryPlan(
        status="ready",
        response_language="en",
        database_dialect=SQLDialect.POSTGRES,
        interpretation="x",
        metric_ids=["revenue"],
        dimension_ids=["region"],
        sql=sql,
    )


SAFE_SQL = (
    "SELECT analytics.orders.region, SUM(analytics.order_items.net_amount) "
    "FROM analytics.orders JOIN analytics.order_items "
    "ON analytics.orders.id = analytics.order_items.order_id "
    "GROUP BY analytics.orders.region "
    "HAVING COUNT(DISTINCT analytics.orders.customer_id) >= 5"
)


async def test_schema_drift_stops_before_explain_and_execution() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    planned = snapshot()
    changed = planned.model_copy(deep=True)
    changed.tables[0].columns.append(
        ColumnMetadata(name="new_column", data_type="text", nullable=True)
    )
    connector = Connector(changed)
    with pytest.raises(Prompt2InsightError) as captured:
        await QueryPlanExecutor().execute(
            plan=plan(SAFE_SQL),
            catalog=catalog,
            schema_snapshot=planned,
            connector=connector,
            settings=Settings(),
        )
    assert captured.value.code is ErrorCode.SCHEMA_CHANGED
    assert not connector.explained and not connector.executed


async def test_current_schema_allows_execution() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    current = snapshot()
    connector = Connector(current)
    await QueryPlanExecutor().execute(
        plan=plan(SAFE_SQL),
        catalog=catalog,
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )
    assert connector.explained and connector.executed


def test_schema_qualified_sources_are_exact_and_ctes_are_excluded() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    policy = QueryPlanExecutor._policy(catalog, snapshot(), Settings())
    validator = SQLValidator()

    accepted = validator.validate(
        sql=(
            "WITH monthly_sales AS (SELECT analytics.orders.id FROM analytics.orders AS o) "
            "SELECT id FROM monthly_sales"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=policy,
    )
    assert accepted.referenced_tables == frozenset({"analytics.orders"})

    for source in ("private.orders", "unknown.orders"):
        with pytest.raises(Prompt2InsightError) as captured:
            validator.validate(
                sql=f"SELECT id FROM {source}",
                dialect=SQLDialect.POSTGRES,
                policy=policy,
            )
        assert captured.value.code is ErrorCode.UNAUTHORIZED_TABLE


def test_policy_intersects_canonical_catalog_and_snapshot_tables() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")

    policy = QueryPlanExecutor._policy(catalog, snapshot(), Settings())

    assert policy.allowed_tables == frozenset({"analytics.orders", "analytics.order_items"})


def test_catalog_tables_preserve_unqualified_column_references() -> None:
    catalog = AnalyticsCatalog.model_validate(
        {
            "catalog_version": "test",
            "metrics": {},
            "dimensions": {},
            "join_contracts": [],
            "column_policies": {"sales.sales": "non_sensitive"},
            "privacy": {"privacy_unit": "sales.customer_id", "minimum_group_size": 1},
        }
    )

    assert QueryPlanExecutor._catalog_tables(catalog, SQLDialect.POSTGRES) == {"sales"}


def test_catalog_tables_preserve_schema_qualified_column_references() -> None:
    catalog = AnalyticsCatalog.model_validate(
        {
            "catalog_version": "test",
            "metrics": {},
            "dimensions": {},
            "join_contracts": [],
            "column_policies": {"analytics.sales.sales": "non_sensitive"},
            "privacy": {"privacy_unit": "analytics.sales.customer_id", "minimum_group_size": 1},
        }
    )

    assert QueryPlanExecutor._catalog_tables(catalog, SQLDialect.POSTGRES) == {
        "analytics.sales"
    }


def test_policy_allows_exact_schema_qualified_postgres_catalog_table() -> None:
    catalog = AnalyticsCatalog.model_validate(
        {
            "catalog_version": "test",
            "metrics": {
                "sales": {
                    "labels": {"en": "Sales", "ar": "Sales"},
                    "aliases": {"en": [], "ar": []},
                    "descriptions": {"en": "", "ar": ""},
                    "expressions": {
                        "postgres": "SUM(analytics.sales.sales)",
                        "mysql": "SUM(sales.sales)",
                    },
                    "allowed_dimensions": [],
                }
            },
            "dimensions": {},
            "join_contracts": [],
            "column_policies": {"analytics.sales.customer_id": "sensitive"},
            "privacy": {
                "privacy_unit": "analytics.sales.customer_id",
                "minimum_group_size": 1,
            },
        }
    )
    sales_snapshot = SchemaSnapshot(
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
                    ColumnMetadata(name="customer_id", data_type="integer", nullable=False),
                ],
            )
        ],
    )

    policy = QueryPlanExecutor._policy(catalog, sales_snapshot, Settings())

    assert policy.allowed_tables == frozenset({"analytics.sales"})


def test_policy_allows_exact_unqualified_mysql_catalog_table() -> None:
    catalog = AnalyticsCatalog.model_validate(
        {
            "catalog_version": "test",
            "metrics": {},
            "dimensions": {},
            "join_contracts": [],
            "column_policies": {"sales.amount": "non_sensitive"},
            "privacy": {"privacy_unit": "sales.id", "minimum_group_size": 1},
        }
    )
    sales_snapshot = SchemaSnapshot(
        dialect=SQLDialect.MYSQL,
        database_name="analytics",
        server_version="8",
        capabilities=DatabaseCapabilities(dialect=SQLDialect.MYSQL, server_version="8"),
        tables=[
            TableMetadata(
                schema_name=None,
                table_name="sales",
                table_type="table",
                columns=[
                    ColumnMetadata(name="amount", data_type="numeric", nullable=False),
                    ColumnMetadata(name="id", data_type="integer", nullable=False),
                ],
            )
        ],
    )

    policy = QueryPlanExecutor._policy(catalog, sales_snapshot, Settings())

    assert policy.allowed_tables == frozenset({"sales"})


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value.__setattr__("metric_ids", ["unknown"]), ErrorCode.METRIC_UNDEFINED),
        (lambda value: value.__setattr__("dimension_ids", ["unknown"]), ErrorCode.METRIC_UNDEFINED),
        (
            lambda value: value.__setattr__("dimension_ids", ["product_category"]),
            ErrorCode.METRIC_POLICY_VIOLATION,
        ),
        (
            lambda value: value.__setattr__(
                "sql",
                SAFE_SQL.replace(
                    "SUM(analytics.order_items.net_amount)", "AVG(analytics.order_items.net_amount)"
                ),
            ),
            ErrorCode.METRIC_POLICY_VIOLATION,
        ),
        (
            lambda value: value.__setattr__(
                "sql", SAFE_SQL.replace("analytics.orders.customer_id", "analytics.orders.id")
            ),
            ErrorCode.PRIVACY_POLICY_VIOLATION,
        ),
        (
            lambda value: value.__setattr__("sql", SAFE_SQL.replace(">= 5", ">= 4")),
            ErrorCode.PRIVACY_POLICY_VIOLATION,
        ),
        (
            lambda value: value.__setattr__(
                "sql",
                SAFE_SQL.replace(" HAVING COUNT(DISTINCT analytics.orders.customer_id) >= 5", ""),
            ),
            ErrorCode.PRIVACY_POLICY_VIOLATION,
        ),
    ],
)
async def test_semantic_and_privacy_policy_rejects_invalid_plan(
    mutate: Callable[[QueryPlan], None], code: ErrorCode
) -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    current = snapshot()
    candidate = plan(SAFE_SQL)
    mutate(candidate)
    connector = Connector(current)
    with pytest.raises(Prompt2InsightError) as captured:
        await QueryPlanExecutor().execute(
            plan=candidate,
            catalog=catalog,
            schema_snapshot=current,
            connector=connector,
            settings=Settings(),
        )
    assert captured.value.code is code
    assert not connector.explained and not connector.executed


async def test_connector_row_bound_and_truncation_metadata() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    current = snapshot()

    class TruncatingConnector(Connector):
        async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
            return QueryResult(
                columns=["region"], rows=[["x"]] * 2, row_count=2, truncated=True, duration_ms=1
            )

    result = await QueryPlanExecutor().execute(
        plan=plan(SAFE_SQL),
        catalog=catalog,
        schema_snapshot=current,
        connector=TruncatingConnector(current),
        settings=Settings(max_output_rows=2),
    )
    assert result.result.row_count <= 2
    assert result.result.truncated
