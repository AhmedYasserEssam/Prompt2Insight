from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import QueryPlan, ResultTable
from app.domain.databases.connector import SQLDatabaseConnector
from app.domain.databases.models import PreparedQuery, QueryResult, SchemaSnapshot
from app.infrastructure.sql.cost_guard import CostPolicy, QueryCostGuard
from app.infrastructure.sql.validator import SQLPolicy, SQLValidator, ValidatedSQL


@dataclass(frozen=True, slots=True)
class ExecutedPlan:
    validated: ValidatedSQL
    result: QueryResult


class QueryPlanExecutor:
    """Validate and execute planner SQL against the approved physical schema snapshot."""

    def __init__(self, validator: SQLValidator | None = None) -> None:
        self._validator = validator or SQLValidator()
        self._cost_guard = QueryCostGuard()

    async def execute(
        self,
        *,
        plan: QueryPlan,
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

        validated = self._validator.validate(
            sql=plan.sql,
            dialect=schema_snapshot.dialect,
            policy=self._policy(schema_snapshot, settings),
        )
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
    def _policy(snapshot: SchemaSnapshot, settings: Settings) -> SQLPolicy:
        tables = frozenset(
            QueryPlanExecutor._snapshot_table_name(table.schema_name, table.table_name)
            for table in snapshot.tables
        )
        columns = frozenset(
            QueryPlanExecutor._snapshot_column_name(
                table.schema_name,
                table.table_name,
                column.name,
            )
            for table in snapshot.tables
            for column in table.columns
        )
        return SQLPolicy(
            allowed_tables=tables,
            allowed_columns=columns,
            maximum_joins=settings.max_joins,
            maximum_rows=settings.max_output_rows,
        )

    @staticmethod
    def _snapshot_table_name(schema_name: str | None, table_name: str) -> str:
        return ".".join(part for part in (schema_name, table_name) if part)

    @staticmethod
    def _snapshot_column_name(schema_name: str | None, table_name: str, column_name: str) -> str:
        return f"{QueryPlanExecutor._snapshot_table_name(schema_name, table_name)}.{column_name}"
