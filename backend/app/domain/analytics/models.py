from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import ErrorCode
from app.domain.databases.models import SQLDialect


class ResponseLanguage(StrEnum):
    AUTO = "auto"
    ENGLISH = "en"
    ARABIC = "ar"


class AnalyticsStatus(StrEnum):
    PLANNED = "planned"
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
    model_config = ConfigDict(extra="forbid")

    dimension_id: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "between"]
    value: Any


QueryParameterValue = str | float | bool | None


class QueryParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: QueryParameterValue


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
    parameters: list[QueryParameter] = Field(default_factory=list)
    clarification_question: str | None = None

    @field_validator("parameters")
    @classmethod
    def parameter_names_must_be_unique(
        cls, parameters: list[QueryParameter]
    ) -> list[QueryParameter]:
        names = [parameter.name for parameter in parameters]
        if len(names) != len(set(names)):
            raise ValueError("Query parameter names must be unique.")
        return parameters

    def parameter_bindings(self) -> dict[str, QueryParameterValue]:
        return {parameter.name: parameter.value for parameter in self.parameters}


class ChartSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "pie", "scatter"]
    x_column: str
    y_columns: list[str] = Field(min_length=1)
    title: str


class ResultTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[list[Any]]


class AnswerOutput(BaseModel):
    """Structured result produced by either answer-model provider."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    insights: list[str] = Field(default_factory=list)
    chart: ChartSpecification | None = None
    warnings: list[str] = Field(default_factory=list)


class ModelExecutionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_model: str | None = None
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    retry_count: int = 0
    generation_stage: str | None = None
    database_dialect: SQLDialect | None = None


class AnalyticsResponse(BaseModel):
    status: AnalyticsStatus
    request_id: UUID
    language: Literal["en", "ar"]
    answer: str | None = None
    insights: list[str] = Field(default_factory=list)
    table: ResultTable | None = None
    chart: ChartSpecification | None = None
    sql: str | None = None
    query_plan: QueryPlan | None = None
    warnings: list[str] = Field(default_factory=list)
    error_code: ErrorCode | None = None
    retryable: bool = False
    model_metadata: ModelExecutionMetadata | None = None
    answer_model_metadata: ModelExecutionMetadata | None = None
