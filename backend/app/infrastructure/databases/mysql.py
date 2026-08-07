import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

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
        async with self._engine.connect() as connection:
            raw = (
                await connection.execute(
                    text(f"EXPLAIN FORMAT=JSON {query.sql}"),
                    query.parameters,
                )
            ).scalar_one()

        plan: dict[str, Any] = raw if isinstance(raw, dict) else json.loads(raw)
        return ExplainResult(raw_plan=plan)

    async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
        started = time.perf_counter()
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

        truncated = len(fetched) > query.maximum_rows
        rows = fetched[: query.maximum_rows]
        return QueryResult(
            columns=columns,
            rows=[list(row) for row in rows],
            row_count=len(rows),
            truncated=truncated,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
