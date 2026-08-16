from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from re import compile as re_compile
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_serializer,
    field_validator,
)

from app.core.errors import ErrorCode, Prompt2InsightError
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


type QueryParameterValue = StrictStr | None


class QueryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_id: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "between"]
    value: QueryParameterValue


type QueryParameterBinding = str | int | float | bool | date | datetime | None

_ISO_DATE = re_compile(r"\d{4}-\d{2}-\d{2}")
_ISO_DATETIME = re_compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?"
)
_CANONICAL_INTEGER = re_compile(r"-?(?:0|[1-9]\d*)")
_CANONICAL_NUMBER = re_compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")


class QueryParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["string", "integer", "number", "boolean", "date", "datetime", "null"]
    value: QueryParameterValue

    def binding_value(self) -> QueryParameterBinding:
        if self.type == "string" and isinstance(self.value, str):
            return self.value
        if (
            self.type == "integer"
            and isinstance(self.value, str)
            and _CANONICAL_INTEGER.fullmatch(self.value)
        ):
            return int(self.value)
        if (
            self.type == "number"
            and isinstance(self.value, str)
            and _CANONICAL_NUMBER.fullmatch(self.value)
        ):
            return float(self.value)
        if self.type == "boolean" and self.value in {"true", "false"}:
            return self.value == "true"
        if self.type == "null" and self.value is None:
            return None
        if self.type == "date" and isinstance(self.value, str) and _ISO_DATE.fullmatch(self.value):
            try:
                return date.fromisoformat(self.value)
            except ValueError as exc:
                raise self._invalid_value() from exc
        if (
            self.type == "datetime"
            and isinstance(self.value, str)
            and _ISO_DATETIME.fullmatch(self.value)
        ):
            try:
                return datetime.fromisoformat(self.value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise self._invalid_value() from exc
        raise self._invalid_value()

    def _invalid_value(self) -> Prompt2InsightError:
        return Prompt2InsightError(
            ErrorCode.INVALID_QUERY_PARAMETER,
            f"Query parameter {self.name!r} does not match declared type {self.type!r}.",
        )


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

    def parameter_bindings(self) -> dict[str, QueryParameterBinding]:
        return {parameter.name: parameter.binding_value() for parameter in self.parameters}


class ChartSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    chart_type: Literal[
        "bar",
        "horizontal_bar",
        "line",
        "area",
        "scatter",
        "donut",
        "kpi",
    ] = Field(alias="type")
    x_column: str | None = None
    y_columns: list[str] = Field(min_length=1)
    series_column: str | None = None
    title: str | None = None
    x_label: str | None = None
    y_label: str | None = None


class ResultTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[list[Any]]

    @field_serializer("rows", when_used="json")
    def serialize_decimal_values_as_json_numbers(
        self, rows: list[list[Any]]
    ) -> list[list[Any]]:
        return [
            [float(value) if isinstance(value, Decimal) else value for value in row]
            for row in rows
        ]


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
