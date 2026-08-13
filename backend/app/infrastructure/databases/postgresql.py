import asyncio
import json
import time
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import (
    DatabaseCapabilities,
    ExplainResult,
    PreparedQuery,
    QueryResult,
    SQLDialect,
    TableMetadata,
)
from app.infrastructure.databases.base import SQLAlchemyConnectorBase


class PostgreSQLConnector(SQLAlchemyConnectorBase):
    @property
    def dialect(self) -> SQLDialect:
        return SQLDialect.POSTGRES

    async def _get_server_version(self, connection: AsyncConnection) -> str:
        return str((await connection.execute(text("SHOW server_version"))).scalar_one())

    async def _get_database_name(self, connection: AsyncConnection) -> str:
        return str((await connection.execute(text("SELECT current_database()"))).scalar_one())

    def _inspect_tables(self, connection: Connection) -> list[TableMetadata]:
        inspector = inspect(connection)
        schemas = self._approved_schemas or tuple(
            schema
            for schema in inspector.get_schema_names()
            if schema != "information_schema" and not schema.startswith("pg_")
        )
        return self._inspect_tables_in_schemas(connection, schemas)

    def _capabilities(self, server_version: str) -> DatabaseCapabilities:
        return DatabaseCapabilities(dialect=self.dialect, server_version=server_version)

    async def explain(self, query: PreparedQuery) -> ExplainResult:
        try:
            async with self._engine.connect() as connection:
                raw = (
                    await connection.execute(
                        text(f"EXPLAIN (FORMAT JSON) {query.sql}"),
                        query.parameters,
                    )
                ).scalar_one()
        except SQLAlchemyError as error:
            raise self._normalize_error(error) from error

        return self._parse_explain(raw)

    @staticmethod
    def _parse_explain(raw: Any) -> ExplainResult:
        try:
            plan = raw if isinstance(raw, list) else json.loads(raw)
            root: dict[str, Any] = plan[0]["Plan"]
            cost = root["Total Cost"]
            rows = root["Plan Rows"]
            if cost is None or rows is None:
                raise ValueError("missing PostgreSQL EXPLAIN estimates")
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise Prompt2InsightError(
                ErrorCode.EXECUTION_FAILED, "The query plan could not be interpreted safely."
            ) from error
        return ExplainResult(
            raw_plan=plan,
            estimated_cost=float(cost),
            estimated_rows=int(rows),
        )

    async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(query.timeout_ms / 1000):
                async with self._engine.connect() as connection:
                    transaction = await connection.begin()
                    try:
                        await connection.execute(text("SET TRANSACTION READ ONLY"))
                        await connection.execute(
                            text("SELECT set_config('lock_timeout', :value, true)"),
                            {"value": f"{query.lock_timeout_ms}ms"},
                        )
                        await connection.execute(
                            text("SELECT set_config('statement_timeout', :value, true)"),
                            {"value": f"{query.timeout_ms}ms"},
                        )
                        result = await connection.execute(text(query.sql), query.parameters)
                        columns = list(result.keys())
                        fetched = result.fetchmany(query.maximum_rows + 1)
                    finally:
                        await transaction.rollback()
        except TimeoutError as error:
            raise Prompt2InsightError(ErrorCode.QUERY_TIMEOUT, "The query timed out.") from error
        except SQLAlchemyError as error:
            raise self._normalize_error(error) from error

        truncated = len(fetched) > query.maximum_rows
        rows = fetched[: query.maximum_rows]
        return QueryResult(
            columns=columns,
            rows=[list(row) for row in rows],
            row_count=len(rows),
            truncated=truncated,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
