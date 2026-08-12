import json
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field


class SQLDialect(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"


class PreparedQuery(BaseModel):
    sql: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    maximum_rows: int = Field(default=1000, ge=1, le=10_000)
    timeout_ms: int = Field(default=8000, ge=100, le=60_000)
    lock_timeout_ms: int = Field(default=2000, ge=0, le=60_000)


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    nullable: bool
    comment: str | None = None


class TableMetadata(BaseModel):
    schema_name: str | None
    table_name: str
    table_type: str
    columns: list[ColumnMetadata]


class DatabaseCapabilities(BaseModel):
    dialect: SQLDialect
    server_version: str
    supports_cte: bool = True
    supports_window_functions: bool = True
    supports_json_explain: bool = True
    supports_read_only_transactions: bool = True
    supports_query_timeout: bool = True


class SchemaSnapshot(BaseModel):
    dialect: SQLDialect
    database_name: str
    server_version: str
    tables: list[TableMetadata]
    capabilities: DatabaseCapabilities

    def fingerprint(self) -> str:
        """Stable schema identity used for plan-to-execution freshness checks."""
        tables = sorted(
            (
                table.schema_name,
                table.table_name,
                table.table_type,
                sorted(
                    (column.name, column.data_type, column.nullable) for column in table.columns
                ),
            )
            for table in self.tables
        )
        encoded = json.dumps(
            {"dialect": self.dialect.value, "database": self.database_name, "tables": tables},
            separators=(",", ":"),
        )
        return sha256(encoded.encode()).hexdigest()


class ExplainResult(BaseModel):
    raw_plan: dict[str, Any] | list[Any]
    estimated_cost: float | None = None
    estimated_rows: int | None = None


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: int
