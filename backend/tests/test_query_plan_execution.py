from collections.abc import Callable
from pathlib import Path

import pytest
from sqlglot import exp, parse_one

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
from app.infrastructure.catalogs.models import AnalyticsCatalog, ColumnClassification
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
        self.explained_sql: str | None = None
        self.executed_sql: str | None = None

    async def get_schema_snapshot(self) -> SchemaSnapshot:
        return self.current

    async def test_connection(self) -> None:
        return None

    async def explain(self, query: PreparedQuery) -> ExplainResult:
        self.explained = True
        self.explained_sql = query.sql
        return ExplainResult(raw_plan={}, estimated_rows=1, estimated_cost=1)

    async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
        self.executed = True
        self.executed_sql = query.sql
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


@pytest.mark.parametrize(
    ("sql", "expected_predicates"),
    [
        (
            SAFE_SQL.replace(" HAVING COUNT(DISTINCT analytics.orders.customer_id) >= 5", ""),
            1,
        ),
        (SAFE_SQL, 1),
        (SAFE_SQL.replace(">= 5", ">= 10"), 1),
        (SAFE_SQL.replace(">= 5", ">= 3"), 2),
        (
            SAFE_SQL.replace(
                "HAVING COUNT(DISTINCT analytics.orders.customer_id) >= 5",
                "HAVING SUM(analytics.order_items.net_amount) > 1000",
            ),
            1,
        ),
    ],
)
async def test_grouped_queries_receive_one_effective_minimum_group_predicate(
    sql: str, expected_predicates: int
) -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    current = snapshot()
    connector = Connector(current)

    execution = await QueryPlanExecutor().execute(
        plan=plan(sql),
        catalog=catalog,
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )

    tree = parse_one(execution.validated.normalized_sql, read="postgres")
    predicates = list(tree.find_all(exp.GTE))
    thresholds = [
        predicate.expression.this
        for predicate in predicates
        if isinstance(predicate.this, exp.Count) and isinstance(predicate.this.this, exp.Distinct)
    ]
    assert len(thresholds) == expected_predicates
    assert any(int(threshold) >= 5 for threshold in thresholds)
    assert connector.explained_sql == execution.validated.normalized_sql
    assert connector.executed_sql == execution.validated.normalized_sql


async def test_grouped_alias_uses_the_query_alias_for_enforced_privacy() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    current = snapshot()
    sql = SAFE_SQL.replace("analytics.orders", "o").replace(
        "FROM o", "FROM analytics.orders AS o"
    ).replace("analytics.order_items", "oi").replace(
        "JOIN oi", "JOIN analytics.order_items AS oi"
    ).replace("analytics.orders.id", "o.id")
    sql = sql.replace("HAVING COUNT(DISTINCT o.customer_id) >= 5", "")
    execution = await QueryPlanExecutor().execute(
        plan=plan(sql),
        catalog=catalog,
        schema_snapshot=current,
        connector=Connector(current),
        settings=Settings(),
    )

    assert "COUNT(DISTINCT o.customer_id) >= 5" in execution.validated.normalized_sql


def test_privacy_verification_rejects_where_and_unrelated_subquery_predicates() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    wrong_where = SAFE_SQL.replace(
        " HAVING COUNT(DISTINCT analytics.orders.customer_id) >= 5",
        " WHERE analytics.orders.customer_id >= 5",
    )
    unrelated_subquery = SAFE_SQL.replace(
        " HAVING COUNT(DISTINCT analytics.orders.customer_id) >= 5",
        " HAVING 1 < (SELECT COUNT(DISTINCT analytics.orders.customer_id) FROM analytics.orders)",
    )

    for sql in (wrong_where, unrelated_subquery):
        with pytest.raises(Prompt2InsightError) as captured:
            QueryPlanExecutor._validate_privacy(sql, catalog, SQLDialect.POSTGRES, plan(sql))
        assert captured.value.code is ErrorCode.PRIVACY_POLICY_VIOLATION


def test_privacy_enforcement_skips_non_grouped_metric_or_dimension_requests() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    sql = "SELECT SUM(analytics.order_items.net_amount) FROM analytics.order_items"
    metric_only = plan(sql)
    metric_only.dimension_ids = []
    dimension_only = plan("SELECT analytics.orders.region FROM analytics.orders")
    dimension_only.metric_ids = []

    assert QueryPlanExecutor._enforce_privacy(metric_only, catalog, SQLDialect.POSTGRES) == sql
    assert (
        QueryPlanExecutor._enforce_privacy(dimension_only, catalog, SQLDialect.POSTGRES)
        == dimension_only.sql
    )


def test_cte_privacy_enforcement_targets_the_grouped_sales_query_block() -> None:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    catalog = catalog.model_copy(deep=True)
    catalog.metrics["revenue"].expressions.postgres = "SUM(analytics.sales.sales)"
    catalog.dimensions["region"].expressions.postgres = (
        "DATE_TRUNC('month', analytics.sales.order_date)"
    )
    catalog.privacy.privacy_unit = "analytics.sales.customer_id"
    grouped = (
        "WITH monthly_sales AS ("
        "SELECT DATE_TRUNC('month', s.order_date) AS month, SUM(s.sales) AS total "
        "FROM analytics.sales AS s GROUP BY DATE_TRUNC('month', s.order_date)"
        ") SELECT monthly_sales.month, monthly_sales.total FROM monthly_sales"
    )
    candidate = plan(grouped)

    effective = QueryPlanExecutor._enforce_privacy(candidate, catalog, SQLDialect.POSTGRES)

    tree = parse_one(effective, read="postgres")
    grouped_select = next(
        select for select in tree.find_all(exp.Select) if select.args.get("group")
    )
    assert grouped_select.args["having"].sql(dialect="postgres") == (
        "HAVING COUNT(DISTINCT s.customer_id) >= 5"
    )
    QueryPlanExecutor._validate_privacy(effective, catalog, SQLDialect.POSTGRES, candidate)


async def test_sales_month_query_executes_with_enforced_privacy_suppression() -> None:
    catalog, current = sales_catalog_and_snapshot()
    candidate = plan(
        "SELECT DATE_TRUNC('month', analytics.sales.order_date), SUM(analytics.sales.sales) "
        "FROM analytics.sales GROUP BY DATE_TRUNC('month', analytics.sales.order_date)"
    )
    connector = Connector(current)

    execution = await QueryPlanExecutor().execute(
        plan=candidate,
        catalog=catalog,
        schema_snapshot=current,
        connector=connector,
        settings=Settings(),
    )

    assert "COUNT(DISTINCT analytics.sales.customer_id) >= 5" in execution.validated.normalized_sql
    assert connector.explained and connector.executed


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT analytics.sales.customer_id, DATE_TRUNC('month', analytics.sales.order_date), "
        "SUM(analytics.sales.sales) FROM analytics.sales "
        "GROUP BY analytics.sales.customer_id, DATE_TRUNC('month', analytics.sales.order_date)",
        "SELECT DATE_TRUNC('month', analytics.sales.order_date), SUM(analytics.sales.sales) "
        "FROM analytics.sales WHERE analytics.sales.customer_id = 1 "
        "GROUP BY DATE_TRUNC('month', analytics.sales.order_date)",
    ],
)
async def test_planner_sensitive_privacy_unit_is_rejected_before_injection(sql: str) -> None:
    catalog, current = sales_catalog_and_snapshot()
    connector = Connector(current)

    with pytest.raises(Prompt2InsightError) as captured:
        await QueryPlanExecutor().execute(
            plan=plan(sql),
            catalog=catalog,
            schema_snapshot=current,
            connector=connector,
            settings=Settings(),
        )

    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN
    assert not connector.explained and not connector.executed


@pytest.mark.parametrize("column", ["customer_name", "internal_note"])
async def test_other_sensitive_and_prohibited_columns_remain_forbidden(column: str) -> None:
    catalog, current = sales_catalog_and_snapshot()
    candidate = plan(
        "SELECT DATE_TRUNC('month', analytics.sales.order_date), SUM(analytics.sales.sales) "
        f"FROM analytics.sales WHERE analytics.sales.{column} = 'x' "
        "GROUP BY DATE_TRUNC('month', analytics.sales.order_date)"
    )

    with pytest.raises(Prompt2InsightError) as captured:
        await QueryPlanExecutor().execute(
            plan=candidate,
            catalog=catalog,
            schema_snapshot=current,
            connector=Connector(current),
            settings=Settings(),
        )

    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_final_policy_exempts_only_the_catalog_privacy_unit() -> None:
    catalog, current = sales_catalog_and_snapshot()
    strict = QueryPlanExecutor._policy(catalog, current, Settings())
    final = QueryPlanExecutor._final_policy(strict, catalog)

    assert "analytics.sales.customer_id" in strict.sensitive_columns
    assert "analytics.sales.customer_id" not in final.sensitive_columns
    assert "analytics.sales.customer_name" in final.sensitive_columns
    assert "analytics.sales.internal_note" in final.prohibited_columns


def sales_catalog_and_snapshot() -> tuple[AnalyticsCatalog, SchemaSnapshot]:
    catalog, _ = load_catalog(Path(__file__).parents[2] / "catalogs/analytics_catalog.example.yaml")
    catalog = catalog.model_copy(deep=True)
    catalog.metrics["revenue"].expressions.postgres = "SUM(analytics.sales.sales)"
    catalog.dimensions["region"].expressions.postgres = (
        "DATE_TRUNC('month', analytics.sales.order_date)"
    )
    catalog.privacy.privacy_unit = "analytics.sales.customer_id"
    catalog.column_policies = {
        "analytics.sales.customer_id": ColumnClassification.SENSITIVE,
        "analytics.sales.customer_name": ColumnClassification.SENSITIVE,
        "analytics.sales.internal_note": ColumnClassification.PROHIBITED,
    }
    return catalog, SchemaSnapshot(
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
                    ColumnMetadata(name="order_date", data_type="timestamp", nullable=False),
                    ColumnMetadata(name="customer_id", data_type="integer", nullable=False),
                    ColumnMetadata(name="customer_name", data_type="text", nullable=False),
                    ColumnMetadata(name="internal_note", data_type="text", nullable=True),
                ],
            )
        ],
    )


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
