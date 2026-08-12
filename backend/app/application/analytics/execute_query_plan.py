from dataclasses import dataclass, replace

from sqlglot import exp, parse_one

from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import QueryPlan, ResultTable
from app.domain.databases.connector import SQLDatabaseConnector
from app.domain.databases.models import PreparedQuery, QueryResult, SchemaSnapshot, SQLDialect
from app.infrastructure.catalogs.models import AnalyticsCatalog
from app.infrastructure.sql.cost_guard import CostPolicy, QueryCostGuard
from app.infrastructure.sql.validator import SQLPolicy, SQLValidator, ValidatedSQL


@dataclass(frozen=True, slots=True)
class ExecutedPlan:
    validated: ValidatedSQL
    result: QueryResult


class QueryPlanExecutor:
    """The sole application boundary that may execute planner-produced SQL."""

    def __init__(self, validator: SQLValidator | None = None) -> None:
        self._validator = validator or SQLValidator()
        self._cost_guard = QueryCostGuard()

    async def execute(
        self,
        *,
        plan: QueryPlan,
        catalog: AnalyticsCatalog,
        schema_snapshot: SchemaSnapshot,
        connector: SQLDatabaseConnector,
        settings: Settings,
    ) -> ExecutedPlan:
        if (
            plan.database_dialect is not schema_snapshot.dialect
            or connector.dialect is not schema_snapshot.dialect
        ):
            raise Prompt2InsightError(ErrorCode.SCHEMA_CHANGED, "The plan dialect is stale.")
        if plan.sql is None:
            raise Prompt2InsightError(ErrorCode.SQL_PARSE_FAILED, "A ready plan requires SQL.")
        current_snapshot = await connector.get_schema_snapshot()
        if current_snapshot.fingerprint() != schema_snapshot.fingerprint():
            raise Prompt2InsightError(
                ErrorCode.SCHEMA_CHANGED, "The schema changed since planning."
            )
        catalog.validate_metric_dimensions(plan.metric_ids, plan.dimension_ids)
        self._validate_canonical_expressions(plan, catalog, schema_snapshot.dialect)
        validated = self._validator.validate(
            sql=plan.sql,
            dialect=schema_snapshot.dialect,
            policy=self._policy(catalog, schema_snapshot, settings),
        )
        self._validate_privacy(validated.normalized_sql, catalog, schema_snapshot.dialect, plan)
        query = PreparedQuery(
            sql=validated.normalized_sql,
            parameters=plan.parameters,
            maximum_rows=settings.max_output_rows,
            timeout_ms=settings.query_timeout_ms,
            lock_timeout_ms=settings.lock_timeout_ms,
        )
        explain = await connector.explain(query)
        self._cost_guard.validate(
            explain, CostPolicy(settings.max_estimated_rows, settings.max_query_cost)
        )
        return ExecutedPlan(validated=validated, result=await connector.execute_read_only(query))

    @staticmethod
    def result_table(result: QueryResult) -> ResultTable:
        return ResultTable(columns=result.columns, rows=result.rows)

    @staticmethod
    def _policy(
        catalog: AnalyticsCatalog, snapshot: SchemaSnapshot, settings: Settings
    ) -> SQLPolicy:
        tables = {
            ".".join(part for part in (table.schema_name, table.table_name) if part)
            for table in snapshot.tables
        }
        columns = frozenset(
            QueryPlanExecutor._snapshot_column_name(
                table.schema_name,
                table.table_name,
                column.name,
            )
            for table in snapshot.tables
            for column in table.columns
        )
        base = SQLPolicy.from_catalog(
            catalog=catalog,
            allowed_tables=frozenset(
                tables & QueryPlanExecutor._catalog_tables(catalog, snapshot.dialect)
            ),
            maximum_joins=settings.max_joins,
            maximum_rows=settings.max_output_rows,
        )
        return replace(base, allowed_columns=columns)

    @staticmethod
    def _snapshot_table_name(schema_name: str | None, table_name: str) -> str:
        return ".".join(part for part in (schema_name, table_name) if part)

    @staticmethod
    def _snapshot_column_name(schema_name: str | None, table_name: str, column_name: str) -> str:
        return f"{QueryPlanExecutor._snapshot_table_name(schema_name, table_name)}.{column_name}"

    @staticmethod
    def _catalog_tables(catalog: AnalyticsCatalog, dialect: SQLDialect) -> set[str]:
        source = [
            *(m.expressions.for_dialect(dialect) for m in catalog.metrics.values()),
            *(d.expressions.for_dialect(dialect) for d in catalog.dimensions.values()),
            catalog.privacy.privacy_unit,
            *catalog.column_policies,
            *(item for join in catalog.join_contracts for item in (join.left, join.right)),
        ]
        result: set[str] = set()
        for value in source:
            tree = parse_one(value, read=dialect.value)
            result.update(
                ".".join(item for item in (table.catalog, table.db, table.name) if item)
                for table in tree.find_all(exp.Table)
            )
            result.update(
                f"{column.db}.{column.table}"
                for column in tree.find_all(exp.Column)
                if column.db and column.table
            )
        return result

    @staticmethod
    def _validate_canonical_expressions(
        plan: QueryPlan, catalog: AnalyticsCatalog, dialect: SQLDialect
    ) -> None:
        tree = parse_one(plan.sql or "", read=dialect.value)
        expressions = {
            QueryPlanExecutor._canonical_sql(node, tree, dialect) for node in tree.walk()
        }
        expected = [catalog.metric_expression(metric, dialect) for metric in plan.metric_ids]
        expected += [
            catalog.dimension_expression(dimension, dialect) for dimension in plan.dimension_ids
        ]
        if any(
            parse_one(value, read=dialect.value).sql(dialect=dialect.value) not in expressions
            for value in expected
        ):
            raise Prompt2InsightError(
                ErrorCode.METRIC_POLICY_VIOLATION,
                "The SQL does not use canonical catalog expressions.",
            )

    @staticmethod
    def _canonical_sql(node: exp.Expression, query: exp.Expression, dialect: SQLDialect) -> str:
        aliases = {
            table.alias_or_name: (table.db, table.name) for table in query.find_all(exp.Table)
        }
        copy = node.copy()
        for column in copy.find_all(exp.Column):
            source = aliases.get(column.table)
            if source and source[0]:
                column.set("db", exp.to_identifier(source[0]))
                column.set("table", exp.to_identifier(source[1]))
        return copy.sql(dialect=dialect.value)

    @staticmethod
    def _validate_privacy(
        sql: str, catalog: AnalyticsCatalog, dialect: SQLDialect, plan: QueryPlan
    ) -> None:
        if not plan.metric_ids or not plan.dimension_ids:
            return
        tree = parse_one(sql, read=dialect.value)
        unit = parse_one(catalog.privacy.privacy_unit, read=dialect.value).sql(
            dialect=dialect.value
        )
        for predicate in tree.find_all(exp.GTE):
            count = predicate.this
            distinct = count.this if isinstance(count, exp.Count) else None
            if (
                isinstance(distinct, exp.Distinct)
                and len(distinct.expressions) == 1
                and QueryPlanExecutor._canonical_sql(distinct.expressions[0], tree, dialect) == unit
                and isinstance(predicate.expression, exp.Literal)
                and int(predicate.expression.this) >= catalog.privacy.minimum_group_size
            ):
                return
        raise Prompt2InsightError(
            ErrorCode.PRIVACY_POLICY_VIOLATION, "The required minimum-group suppression is missing."
        )
