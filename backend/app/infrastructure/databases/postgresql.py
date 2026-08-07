import asyncio
import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import (
    DatabaseCapabilities,
    ExplainResult,
    PreparedQuery,
    QueryResult,
    SQLDialect,
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

        plan = raw if isinstance(raw, list) else json.loads(raw)
        root: dict[str, Any] = plan[0]["Plan"]
        return ExplainResult(
            raw_plan=plan,
            estimated_cost=float(root.get("Total Cost", 0)),
            estimated_rows=int(root.get("Plan Rows", 0)),
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
