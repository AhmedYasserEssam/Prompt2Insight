from pydantic import BaseModel, ConfigDict, Field

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


class DimensionDefinition(BaseModel):
    labels: LocalizedText
    aliases: LocalizedAliases
    descriptions: LocalizedText
    expressions: DialectExpressions


class AnalyticsCatalog(BaseModel):
    # Old catalogs may contain policy-era keys such as allowed_dimensions,
    # join_contracts, column_policies, and privacy. Pydantic ignores those legacy
    # fields during deserialization; they no longer participate in planning or execution.
    model_config = ConfigDict(extra="ignore")

    catalog_version: str
    metrics: dict[str, MetricDefinition]
    dimensions: dict[str, DimensionDefinition]

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
