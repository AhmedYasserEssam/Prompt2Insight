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


class MySQLConnector(SQLAlchemyConnectorBase):
    @property
    def dialect(self) -> SQLDialect:
        return SQLDialect.MYSQL

    async def _get_server_version(self, connection: AsyncConnection) -> str:
        return str((await connection.execute(text("SELECT VERSION()"))).scalar_one())

    async def _get_database_name(self, connection: AsyncConnection) -> str:
        return str((await connection.execute(text("SELECT DATABASE()"))).scalar_one())

    def _capabilities(self, server_version: str) -> DatabaseCapabilities:
        return DatabaseCapabilities(dialect=self.dialect, server_version=server_version)

    async def explain(self, query: PreparedQuery) -> ExplainResult:
        try:
            async with self._engine.connect() as connection:
                raw = (
                    await connection.execute(
                        text(f"EXPLAIN FORMAT=JSON {query.sql}"), query.parameters
                    )
                ).scalar_one()
        except SQLAlchemyError as error:
            raise self._normalize_error(error) from error
        return self._parse_explain(raw)

    @staticmethod
    def _parse_explain(raw: Any) -> ExplainResult:
        try:
            plan: dict[str, Any] = raw if isinstance(raw, dict) else json.loads(raw)
            root = plan["query_block"]
            cost = root.get("cost_info", {}).get("query_cost")
            rows = MySQLConnector._estimated_rows(root)
            if cost is None or rows is None:
                raise ValueError("missing MySQL EXPLAIN estimates")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise Prompt2InsightError(
                ErrorCode.EXECUTION_FAILED, "The query plan could not be interpreted safely."
            ) from error
        return ExplainResult(
            raw_plan=plan,
            estimated_cost=float(cost),
            estimated_rows=rows,
        )

    async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(query.timeout_ms / 1000):
                async with self._engine.connect() as connection:
                    await connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
                    await connection.commit()
                    await connection.exec_driver_sql(
                        f"SET SESSION MAX_EXECUTION_TIME={int(query.timeout_ms)}"
                    )
                    await connection.commit()
                    transaction = await connection.begin()
                    try:
                        result = await connection.execute(text(query.sql), query.parameters)
                        columns = list(result.keys())
                        fetched = result.fetchmany(query.maximum_rows + 1)
                    finally:
                        await transaction.rollback()
                        await connection.exec_driver_sql("SET SESSION TRANSACTION READ WRITE")
                        await connection.exec_driver_sql("SET SESSION MAX_EXECUTION_TIME=0")
                        await connection.commit()
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

    @staticmethod
    def _estimated_rows(node: dict[str, Any]) -> int | None:
        if "rows_produced_per_join" in node:
            return int(node["rows_produced_per_join"])
        if "rows_examined_per_scan" in node:
            return int(node["rows_examined_per_scan"])
        for value in node.values():
            if isinstance(value, dict):
                rows = MySQLConnector._estimated_rows(value)
                if rows is not None:
                    return rows
            if isinstance(value, list):
                for child in value:
                    if isinstance(child, dict):
                        rows = MySQLConnector._estimated_rows(child)
                        if rows is not None:
                            return rows
        return None
