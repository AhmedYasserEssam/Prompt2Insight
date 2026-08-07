from abc import abstractmethod
from collections.abc import Sequence
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.domain.databases.connector import SQLDatabaseConnector
from app.domain.databases.models import (
    ColumnMetadata,
    DatabaseCapabilities,
    SchemaSnapshot,
    TableMetadata,
)


class SQLAlchemyConnectorBase(SQLDatabaseConnector):
    def __init__(
        self,
        database_url: str,
        *,
        approved_schemas: Sequence[str] | None = None,
    ) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self._approved_schemas = tuple(approved_schemas or ())

    async def test_connection(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def get_schema_snapshot(self) -> SchemaSnapshot:
        async with self._engine.connect() as connection:
            version = await self._get_server_version(connection)
            database = await self._get_database_name(connection)
            tables = await connection.run_sync(self._inspect_tables)

        return SchemaSnapshot(
            dialect=self.dialect,
            database_name=database,
            server_version=version,
            tables=tables,
            capabilities=self._capabilities(version),
        )

    def _inspect_tables(self, connection: Connection) -> list[TableMetadata]:
        inspector = inspect(connection)
        schemas: Sequence[str | None] = self._approved_schemas or (None,)
        output: list[TableMetadata] = []

        for schema in schemas:
            names = [
                ("table", name) for name in inspector.get_table_names(schema=schema)
            ]
            names += [
                ("view", name) for name in inspector.get_view_names(schema=schema)
            ]

            for table_type, name in names:
                columns = [
                    ColumnMetadata(
                        name=str(column["name"]),
                        data_type=str(column["type"]),
                        nullable=bool(column.get("nullable", True)),
                        comment=column.get("comment"),
                    )
                    for column in inspector.get_columns(name, schema=schema)
                ]
                output.append(
                    TableMetadata(
                        schema_name=schema,
                        table_name=name,
                        table_type=table_type,
                        columns=columns,
                    )
                )

        return output

    @abstractmethod
    async def _get_server_version(self, connection: Any) -> str:
        raise NotImplementedError

    @abstractmethod
    async def _get_database_name(self, connection: Any) -> str:
        raise NotImplementedError

    @abstractmethod
    def _capabilities(self, server_version: str) -> DatabaseCapabilities:
        raise NotImplementedError

    async def close(self) -> None:
        await self._engine.dispose()
