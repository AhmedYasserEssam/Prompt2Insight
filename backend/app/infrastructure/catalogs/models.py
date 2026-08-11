from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect
from app.infrastructure.i18n.arabic import normalize_arabic_for_lookup


class LocalizedText(BaseModel):
    en: str
    ar: str


class LocalizedAliases(BaseModel):
    en: list[str] = Field(default_factory=list)
    ar: list[str] = Field(default_factory=list)


class DialectExpressions(BaseModel):
    postgres: str
    mysql: str

    def for_dialect(self, dialect: SQLDialect) -> str:
        return self.postgres if dialect is SQLDialect.POSTGRES else self.mysql


class MetricDefinition(BaseModel):
    labels: LocalizedText
    aliases: LocalizedAliases
    descriptions: LocalizedText
    expressions: DialectExpressions
    allowed_dimensions: list[str]


class DimensionDefinition(BaseModel):
    labels: LocalizedText
    aliases: LocalizedAliases
    descriptions: LocalizedText
    expressions: DialectExpressions


class JoinContract(BaseModel):
    left: str
    right: str
    relationship: str
    allowed_types: list[str]


class ColumnClassification(StrEnum):
    NON_SENSITIVE = "non_sensitive"
    SENSITIVE = "sensitive"
    PROHIBITED = "prohibited"


class PrivacyDefinition(BaseModel):
    privacy_unit: str
    minimum_group_size: int = Field(ge=1)

    def suppresses(self, group_size: int) -> bool:
        return group_size < self.minimum_group_size


class AnalyticsCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_version: str
    metrics: dict[str, MetricDefinition]
    dimensions: dict[str, DimensionDefinition]
    join_contracts: list[JoinContract]
    column_policies: dict[str, ColumnClassification]
    privacy: PrivacyDefinition

    @model_validator(mode="after")
    def validate_references(self) -> "AnalyticsCatalog":
        unknown_dimensions = {
            dimension_id
            for metric in self.metrics.values()
            for dimension_id in metric.allowed_dimensions
            if dimension_id not in self.dimensions
        }
        if unknown_dimensions:
            raise ValueError(
                "Metrics reference undefined dimensions: "
                + ", ".join(sorted(unknown_dimensions))
            )
        return self

    def resolve_metric_id(self, alias: str) -> str:
        return self._resolve_alias(alias, self.metrics, ErrorCode.METRIC_UNDEFINED, "metric")

    def resolve_dimension_id(self, alias: str) -> str:
        return self._resolve_alias(
            alias, self.dimensions, ErrorCode.METRIC_UNDEFINED, "dimension"
        )

    def metric_expression(self, metric_id: str, dialect: SQLDialect) -> str:
        try:
            metric = self.metrics[metric_id]
        except KeyError as exc:
            raise Prompt2InsightError(
                ErrorCode.METRIC_UNDEFINED, f"Undefined metric: {metric_id}."
            ) from exc
        return metric.expressions.for_dialect(dialect)

    def dimension_expression(self, dimension_id: str, dialect: SQLDialect) -> str:
        try:
            dimension = self.dimensions[dimension_id]
        except KeyError as exc:
            raise Prompt2InsightError(
                ErrorCode.METRIC_UNDEFINED, f"Undefined dimension: {dimension_id}."
            ) from exc
        return dimension.expressions.for_dialect(dialect)

    def validate_metric_dimensions(
        self, metric_ids: Iterable[str], dimension_ids: Iterable[str]
    ) -> None:
        requested_dimensions = set(dimension_ids)
        unknown_dimensions = requested_dimensions - self.dimensions.keys()
        if unknown_dimensions:
            raise Prompt2InsightError(
                ErrorCode.METRIC_UNDEFINED,
                f"Undefined dimensions: {', '.join(sorted(unknown_dimensions))}.",
            )

        for metric_id in metric_ids:
            try:
                metric = self.metrics[metric_id]
            except KeyError as exc:
                raise Prompt2InsightError(
                    ErrorCode.METRIC_UNDEFINED, f"Undefined metric: {metric_id}."
                ) from exc
            unsupported = requested_dimensions - set(metric.allowed_dimensions)
            if unsupported:
                raise Prompt2InsightError(
                    ErrorCode.METRIC_UNDEFINED,
                    f"Metric {metric_id} does not support dimensions: "
                    f"{', '.join(sorted(unsupported))}.",
                )

    def sql_join_contracts(self) -> frozenset[tuple[str, str, str]]:
        return frozenset(
            (contract.left, contract.right, join_type.lower())
            for contract in self.join_contracts
            for join_type in contract.allowed_types
        )

    @staticmethod
    def _resolve_alias[T: MetricDefinition | DimensionDefinition](
        alias: str,
        definitions: dict[str, T],
        error_code: ErrorCode,
        entity_name: str,
    ) -> str:
        normalized_alias = normalize_arabic_for_lookup(alias)
        matches = [
            definition_id
            for definition_id, definition in definitions.items()
            if normalized_alias
            in {
                normalize_arabic_for_lookup(value)
                for value in (
                    definition.labels.en,
                    definition.labels.ar,
                    *definition.aliases.en,
                    *definition.aliases.ar,
                )
            }
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise Prompt2InsightError(
                error_code, f"Ambiguous {entity_name} alias: {alias}."
            )
        raise Prompt2InsightError(error_code, f"Undefined {entity_name}: {alias}.")
