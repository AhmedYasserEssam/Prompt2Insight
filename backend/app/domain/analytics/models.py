from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ErrorCode
from app.domain.databases.models import SQLDialect


class ResponseLanguage(StrEnum):
    AUTO = "auto"
    ENGLISH = "en"
    ARABIC = "ar"


class AnalyticsStatus(StrEnum):
    SUCCESS = "success"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    EMPTY_RESULT = "empty_result"
    FAILED = "failed"


class AnalyticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: UUID
    question: str = Field(min_length=1, max_length=4000)
    response_language: ResponseLanguage = ResponseLanguage.AUTO


class QueryFilter(BaseModel):
    dimension_id: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "between"]
    value: Any


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "needs_clarification", "unsupported"]
    response_language: Literal["en", "ar"]
    database_dialect: SQLDialect
    interpretation: str
    metric_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    sql: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    clarification_question: str | None = None


class ChartSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "pie", "scatter"]
    x_column: str
    y_columns: list[str] = Field(min_length=1)
    title: str


class ResultTable(BaseModel):
    columns: list[str]
    rows: list[list[Any]]


class AnalyticsResponse(BaseModel):
    status: AnalyticsStatus
    request_id: UUID
    language: Literal["en", "ar"]
    answer: str | None = None
    insights: list[str] = Field(default_factory=list)
    table: ResultTable | None = None
    chart: ChartSpecification | None = None
    sql: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error_code: ErrorCode | None = None
    retryable: bool = False
