from abc import ABC, abstractmethod

from app.domain.databases.models import (
    ExplainResult,
    PreparedQuery,
    QueryResult,
    SchemaSnapshot,
    SQLDialect,
)


class SQLDatabaseConnector(ABC):
    @property
    @abstractmethod
    def dialect(self) -> SQLDialect:
        """Return the SQL dialect implemented by this connector."""

    @abstractmethod
    async def test_connection(self) -> None:
        """Raise a normalized error when connection testing fails."""

    @abstractmethod
    async def get_schema_snapshot(self) -> SchemaSnapshot:
        """Return the schema visible to the restricted analytics user."""

    @abstractmethod
    async def explain(self, query: PreparedQuery) -> ExplainResult:
        """Return a database query plan without executing the query."""

    @abstractmethod
    async def execute_read_only(self, query: PreparedQuery) -> QueryResult:
        """Execute prevalidated SQL inside a read-only transaction."""

    @abstractmethod
    async def close(self) -> None:
        """Dispose of pools and resources."""
