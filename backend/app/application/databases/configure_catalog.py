from uuid import UUID

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.connection_profiles import CatalogStatus, CatalogValidationResult
from app.domain.databases.models import SchemaSnapshot, SQLDialect
from app.infrastructure.catalogs.models import AnalyticsCatalog
from app.persistence.models import SchemaSnapshotRecord
from app.persistence.repositories import ConnectionProfileRepository


class CatalogConfigurationService:
    def __init__(self, repository: ConnectionProfileRepository) -> None:
        self._repository = repository

    async def status(self, profile_id: UUID) -> CatalogStatus:
        snapshot_record = await self._snapshot_record(profile_id)
        snapshot = SchemaSnapshot.model_validate(snapshot_record.snapshot)
        saved = await self._repository.get_catalog(profile_id)
        if saved is None:
            return CatalogStatus(schema_snapshot=snapshot, state="catalog_needs_configuration")
        catalog, content_hash, schema_snapshot_id = saved
        return CatalogStatus(
            catalog=catalog,
            schema_snapshot=snapshot,
            state=("ready" if schema_snapshot_id == snapshot_record.id else "stale"),
            content_hash=content_hash,
        )

    async def validate(
        self, profile_id: UUID, catalog: AnalyticsCatalog
    ) -> CatalogValidationResult:
        snapshot = await self._snapshot(profile_id)
        _, errors = self._canonical_catalog(catalog, snapshot)
        return CatalogValidationResult(valid=not errors, errors=errors)

    async def publish(self, profile_id: UUID, catalog: AnalyticsCatalog) -> CatalogStatus:
        snapshot_record = await self._snapshot_record(profile_id)
        snapshot = SchemaSnapshot.model_validate(snapshot_record.snapshot)
        canonical_catalog, errors = self._canonical_catalog(catalog, snapshot)
        if errors:
            raise Prompt2InsightError(
                ErrorCode.CATALOG_NOT_READY, "; ".join(errors)
            )
        content_hash = await self._repository.publish_catalog(
            profile_id, canonical_catalog, snapshot_record.id
        )
        return CatalogStatus(
            catalog=canonical_catalog,
            schema_snapshot=snapshot,
            state="ready",
            content_hash=content_hash,
        )

    async def _snapshot(self, profile_id: UUID) -> SchemaSnapshot:
        record = await self._snapshot_record(profile_id)
        return SchemaSnapshot.model_validate(record.snapshot)

    async def _snapshot_record(self, profile_id: UUID) -> SchemaSnapshotRecord:
        record = await self._repository.get_schema_snapshot_record(profile_id)
        if record is None:
            raise Prompt2InsightError(
                ErrorCode.NOT_CONFIGURED,
                "No schema snapshot is available for this connection profile.",
            )
        return record

    @staticmethod
    def _canonical_catalog(
        catalog: AnalyticsCatalog, snapshot: SchemaSnapshot
    ) -> tuple[AnalyticsCatalog, list[str]]:
        canonical = catalog.model_copy(deep=True)
        table_schemas: dict[str, list[str | None]] = {
            table.table_name: [] for table in snapshot.tables
        }
        for table in snapshot.tables:
            table_schemas[table.table_name].append(table.schema_name)
        errors: list[str] = []

        def canonicalize(
            value: str, label: str, dialect: SQLDialect, report_errors: bool
        ) -> str:
            try:
                tree = parse_one(value, read=dialect.value)
            except ParseError:
                if report_errors:
                    errors.append(
                        f"{label}: expression cannot be parsed for {dialect.value}."
                    )
                return value
            expression_errors: list[str] = []
            for table in tree.find_all(exp.Table):
                CatalogConfigurationService._qualify_table(
                    table, table_schemas, expression_errors, label
                )
            for column in tree.find_all(exp.Column):
                if column.table:
                    CatalogConfigurationService._qualify_column(
                        column, table_schemas, expression_errors, label
                    )
            if report_errors:
                errors.extend(expression_errors)
            return tree.sql(dialect=dialect.value)

        for metric_id, metric in canonical.metrics.items():
            expressions = metric.expressions
            expressions.postgres = canonicalize(
                expressions.postgres,
                f'Metric "{metric_id}"',
                SQLDialect.POSTGRES,
                snapshot.dialect is SQLDialect.POSTGRES,
            )
            expressions.mysql = canonicalize(
                expressions.mysql,
                f'Metric "{metric_id}"',
                SQLDialect.MYSQL,
                snapshot.dialect is SQLDialect.MYSQL,
            )
        for dimension_id, dimension in canonical.dimensions.items():
            expressions = dimension.expressions
            expressions.postgres = canonicalize(
                expressions.postgres,
                f'Dimension "{dimension_id}"',
                SQLDialect.POSTGRES,
                snapshot.dialect is SQLDialect.POSTGRES,
            )
            expressions.mysql = canonicalize(
                expressions.mysql,
                f'Dimension "{dimension_id}"',
                SQLDialect.MYSQL,
                snapshot.dialect is SQLDialect.MYSQL,
            )
        errors.extend(CatalogConfigurationService._validate_catalog(canonical, snapshot))
        return canonical, errors

    @staticmethod
    def _qualify_table(
        table: exp.Table, table_schemas: dict[str, list[str | None]], errors: list[str], label: str
    ) -> None:
        if table.db:
            if table.db not in table_schemas.get(table.name, []):
                errors.append(f"{label}: table {table.db}.{table.name} does not exist.")
            return
        schemas = table_schemas.get(table.name, [])
        if len(schemas) == 1:
            if schemas[0]:
                table.set("db", exp.to_identifier(schemas[0]))
        elif len(schemas) > 1:
            errors.append(f"{label}: table {table.name} is ambiguous across schemas.")
        else:
            errors.append(f"{label}: table {table.name} does not exist.")

    @staticmethod
    def _qualify_column(
        column: exp.Column,
        table_schemas: dict[str, list[str | None]],
        errors: list[str],
        label: str,
    ) -> None:
        if column.db:
            if column.db not in table_schemas.get(column.table, []):
                errors.append(f"{label}: table {column.db}.{column.table} does not exist.")
            return
        schemas = table_schemas.get(column.table, [])
        if len(schemas) == 1:
            if schemas[0]:
                column.set("db", exp.to_identifier(schemas[0]))
        elif len(schemas) > 1:
            errors.append(f"{label}: table {column.table} is ambiguous across schemas.")
        else:
            errors.append(f"{label}: table {column.table} does not exist.")

    @staticmethod
    def _validate_catalog(catalog: AnalyticsCatalog, snapshot: SchemaSnapshot) -> list[str]:
        columns = {
            ".".join(part for part in (table.schema_name, table.table_name, column.name) if part)
            for table in snapshot.tables
            for column in table.columns
        }
        tables = {
            ".".join(part for part in (table.schema_name, table.table_name) if part)
            for table in snapshot.tables
        }
        errors: list[str] = []
        for metric_id, metric in catalog.metrics.items():
            errors.extend(CatalogConfigurationService._expression_errors(
                f'Metric "{metric_id}"', metric.expressions.for_dialect(snapshot.dialect),
                snapshot.dialect.value, columns, tables
            ))
        for dimension_id, dimension in catalog.dimensions.items():
            errors.extend(CatalogConfigurationService._expression_errors(
                f'Dimension "{dimension_id}"', dimension.expressions.for_dialect(snapshot.dialect),
                snapshot.dialect.value, columns, tables
            ))
        return errors

    @staticmethod
    def _expression_errors(
        label: str, expression: str, dialect: str, columns: set[str], tables: set[str]
    ) -> list[str]:
        try:
            tree = parse_one(expression, read=dialect)
        except ParseError:
            return [f"{label}: expression cannot be parsed for {dialect}."]
        errors: list[str] = []
        for column in tree.find_all(exp.Column):
            qualifier = ".".join(part for part in (column.db, column.table, column.name) if part)
            matches = [
                item for item in columns if item == qualifier
            ]
            if not matches:
                errors.append(f"{label}: column {qualifier} does not exist.")
        for table in tree.find_all(exp.Table):
            name = ".".join(part for part in (table.db, table.name) if part)
            if name and name not in tables:
                errors.append(f"{label}: table {name} does not exist.")
        return errors
