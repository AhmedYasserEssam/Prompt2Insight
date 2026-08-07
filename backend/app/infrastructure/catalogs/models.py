from pydantic import BaseModel, Field

from app.domain.databases.models import SQLDialect


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
        return getattr(self, dialect.value)


class MetricDefinition(BaseModel):
    labels: LocalizedText
    aliases: LocalizedAliases
    descriptions: LocalizedText
    expressions: DialectExpressions
    allowed_dimensions: list[str]


class DimensionDefinition(BaseModel):
    labels: LocalizedText
    aliases: LocalizedAliases
    expressions: DialectExpressions


class JoinContract(BaseModel):
    left: str
    right: str
    relationship: str
    allowed_types: list[str]


class PrivacyDefinition(BaseModel):
    privacy_unit: str
    minimum_group_size: int = Field(ge=1)


class AnalyticsCatalog(BaseModel):
    catalog_version: str
    metrics: dict[str, MetricDefinition]
    dimensions: dict[str, DimensionDefinition]
    join_contracts: list[JoinContract]
    column_policies: dict[str, str]
    privacy: PrivacyDefinition
