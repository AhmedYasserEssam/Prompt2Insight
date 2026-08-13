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
        strict_policy = self._policy(catalog, schema_snapshot, settings)
        self._validator.validate(
            sql=plan.sql,
            dialect=schema_snapshot.dialect,
            policy=strict_policy,
        )
        effective_sql = self._enforce_privacy(plan, catalog, schema_snapshot.dialect)
        validated = self._validator.validate(
            sql=effective_sql,
            dialect=schema_snapshot.dialect,
            policy=self._final_policy(strict_policy, catalog),
        )
        self._validate_privacy(validated.normalized_sql, catalog, schema_snapshot.dialect, plan)
        query = PreparedQuery(
            sql=validated.normalized_sql,
            parameters=plan.parameter_bindings(),
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
    def _final_policy(strict_policy: SQLPolicy, catalog: AnalyticsCatalog) -> SQLPolicy:
        """Permit only the trusted privacy unit added after strict planner validation."""
        return replace(
            strict_policy,
            sensitive_columns=strict_policy.sensitive_columns - {catalog.privacy.privacy_unit},
        )

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
            for column in tree.find_all(exp.Column):
                if column.db and column.table:
                    result.add(f"{column.db}.{column.table}")
                elif column.table:
                    result.add(column.table)
        return result

    @staticmethod
    def _validate_canonical_expressions(
        plan: QueryPlan, catalog: AnalyticsCatalog, dialect: SQLDialect
    ) -> None:
        tree = parse_one(plan.sql or "", read=dialect.value)
        expressions = {
            QueryPlanExecutor._canonical_sql(
                node, node.find_ancestor(exp.Select) or tree, dialect
            )
            for node in tree.walk()
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
        aliases = QueryPlanExecutor._scope_aliases(query)
        copy = node.copy()
        for column in copy.find_all(exp.Column):
            source = aliases.get(column.table)
            if source and source[0]:
                column.set("db", exp.to_identifier(source[0]))
                column.set("table", exp.to_identifier(source[1]))
        return copy.sql(dialect=dialect.value)

    @staticmethod
    def _scope_aliases(query: exp.Expression) -> dict[str, tuple[str, str]]:
        return {
            table.alias_or_name: (table.db, table.name)
            for table in query.find_all(exp.Table)
            if table.find_ancestor(exp.Select) is query
        }

    @staticmethod
    def _aggregation_scope(
        tree: exp.Expression, plan: QueryPlan, catalog: AnalyticsCatalog, dialect: SQLDialect
    ) -> exp.Select:
        expected = {
            parse_one(catalog.metric_expression(metric, dialect), read=dialect.value).sql(
                dialect=dialect.value
            )
            for metric in plan.metric_ids
        }
        expected.update(
            parse_one(catalog.dimension_expression(dimension, dialect), read=dialect.value).sql(
                dialect=dialect.value
            )
            for dimension in plan.dimension_ids
        )
        candidates: list[exp.Select] = []
        for select in tree.find_all(exp.Select):
            if select.args.get("group") is None:
                continue
            expressions = {
                QueryPlanExecutor._canonical_sql(node, select, dialect)
                for projection in select.expressions
                for node in projection.walk()
            }
            if expected <= expressions:
                candidates.append(select)
        if len(candidates) != 1:
            raise Prompt2InsightError(
                ErrorCode.PRIVACY_POLICY_VIOLATION,
                "The grouped query scope for privacy suppression is ambiguous.",
            )
        return candidates[0]

    @staticmethod
    def _privacy_column(
        select: exp.Select, catalog: AnalyticsCatalog, dialect: SQLDialect
    ) -> exp.Column:
        unit = parse_one(catalog.privacy.privacy_unit, read=dialect.value)
        if not isinstance(unit, exp.Column) or not unit.table:
            raise Prompt2InsightError(
                ErrorCode.PRIVACY_POLICY_VIOLATION, "The catalog privacy unit is invalid."
            )
        expected_table = ".".join(part for part in (unit.db, unit.table) if part)
        sources = [
            table
            for table in select.find_all(exp.Table)
            if table.find_ancestor(exp.Select) is select
            and QueryPlanExecutor._snapshot_table_name(table.db, table.name) == expected_table
        ]
        if len(sources) != 1:
            raise Prompt2InsightError(
                ErrorCode.PRIVACY_POLICY_VIOLATION,
                "The privacy unit source is unavailable or ambiguous in the grouped query.",
            )
        source = sources[0]
        if source.alias:
            return exp.column(unit.name, table=source.alias_or_name)
        return unit.copy()

    @staticmethod
    def _privacy_predicates(
        select: exp.Select, unit: str, dialect: SQLDialect
    ) -> list[exp.GTE]:
        having = select.args.get("having")
        if not isinstance(having, exp.Having):
            return []
        return [
            predicate
            for predicate in having.find_all(exp.GTE)
            if predicate.find_ancestor(exp.Select) is select
            and QueryPlanExecutor._privacy_threshold(predicate, select, unit, dialect) is not None
        ]

    @staticmethod
    def _privacy_threshold(
        predicate: exp.GTE, select: exp.Select, unit: str, dialect: SQLDialect
    ) -> int | None:
        count = predicate.this
        distinct = count.this if isinstance(count, exp.Count) else None
        if (
            not isinstance(distinct, exp.Distinct)
            or len(distinct.expressions) != 1
            or QueryPlanExecutor._canonical_sql(distinct.expressions[0], select, dialect) != unit
            or not isinstance(predicate.expression, exp.Literal)
        ):
            return None
        try:
            return int(predicate.expression.this)
        except ValueError:
            return None

    @staticmethod
    def _enforce_privacy(plan: QueryPlan, catalog: AnalyticsCatalog, dialect: SQLDialect) -> str:
        if not plan.metric_ids or not plan.dimension_ids:
            return plan.sql or ""
        tree = parse_one(plan.sql or "", read=dialect.value)
        select = QueryPlanExecutor._aggregation_scope(tree, plan, catalog, dialect)
        unit = parse_one(catalog.privacy.privacy_unit, read=dialect.value).sql(
            dialect=dialect.value
        )
        if any(
            threshold >= catalog.privacy.minimum_group_size
            for predicate in QueryPlanExecutor._privacy_predicates(select, unit, dialect)
            if (threshold := QueryPlanExecutor._privacy_threshold(predicate, select, unit, dialect))
            is not None
        ):
            return tree.sql(dialect=dialect.value)
        predicate = exp.GTE(
            this=exp.Count(
                this=exp.Distinct(
                    expressions=[QueryPlanExecutor._privacy_column(select, catalog, dialect)]
                )
            ),
            expression=exp.Literal.number(catalog.privacy.minimum_group_size),
        )
        having = select.args.get("having")
        if isinstance(having, exp.Having):
            having.set("this", exp.and_(having.this, predicate))
        else:
            select.set("having", exp.Having(this=predicate))
        return tree.sql(dialect=dialect.value)

    @staticmethod
    def _validate_privacy(
        sql: str, catalog: AnalyticsCatalog, dialect: SQLDialect, plan: QueryPlan
    ) -> None:
        if not plan.metric_ids or not plan.dimension_ids:
            return
        tree = parse_one(sql, read=dialect.value)
        select = QueryPlanExecutor._aggregation_scope(tree, plan, catalog, dialect)
        unit = parse_one(catalog.privacy.privacy_unit, read=dialect.value).sql(
            dialect=dialect.value
        )
        if any(
            threshold >= catalog.privacy.minimum_group_size
            for predicate in QueryPlanExecutor._privacy_predicates(select, unit, dialect)
            if (threshold := QueryPlanExecutor._privacy_threshold(predicate, select, unit, dialect))
            is not None
        ):
            return
        raise Prompt2InsightError(
            ErrorCode.PRIVACY_POLICY_VIOLATION, "The required minimum-group suppression is missing."
        )
