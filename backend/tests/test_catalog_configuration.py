from types import SimpleNamespace
from uuid import UUID, uuid4

from app.application.databases.configure_catalog import CatalogConfigurationService
from app.domain.databases.models import (
    ColumnMetadata,
    DatabaseCapabilities,
    SchemaSnapshot,
    SQLDialect,
    TableMetadata,
)
from app.infrastructure.catalogs.models import AnalyticsCatalog


def catalog() -> AnalyticsCatalog:
    return AnalyticsCatalog.model_validate(
        {
            "catalog_version": "1",
            "metrics": {
                "total_revenue": {
                    "labels": {"en": "Total revenue", "ar": "إجمالي الإيرادات"},
                    "aliases": {"en": ["revenue"], "ar": ["الإيرادات"]},
                    "descriptions": {"en": "", "ar": ""},
                    "expressions": {
                        "postgres": "SUM(orders.amount)", "mysql": "SUM(orders.amount)"
                    },
                    "allowed_dimensions": ["order_month"],
                }
            },
            "dimensions": {
                "order_month": {
                    "labels": {"en": "Order month", "ar": "شهر الطلب"},
                    "aliases": {"en": ["month"], "ar": ["الشهر"]},
                    "descriptions": {"en": "", "ar": ""},
                    "expressions": {"postgres": "orders.order_date", "mysql": "orders.order_date"},
                }
            },
            "join_contracts": [
                {
                    "left": "orders.customer_id", "right": "customers.id",
                    "relationship": "many_to_one", "allowed_types": ["inner", "left"],
                }
            ],
            "column_policies": {"customers.email": "sensitive"},
            "privacy": {"privacy_unit": "orders.customer_id", "minimum_group_size": 5},
        }
    )


class RepositoryStub:
    def __init__(self) -> None:
        self.snapshot = SchemaSnapshot(
            dialect=SQLDialect.POSTGRES, database_name="sales", server_version="16",
            capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
            tables=[
                TableMetadata(schema_name=None, table_name="orders", table_type="table", columns=[
                    ColumnMetadata(name="id", data_type="integer", nullable=False),
                    ColumnMetadata(name="amount", data_type="numeric", nullable=False),
                    ColumnMetadata(name="order_date", data_type="date", nullable=False),
                    ColumnMetadata(name="customer_id", data_type="integer", nullable=False),
                ]),
                TableMetadata(
                    schema_name=None, table_name="customers", table_type="table", columns=[
                        ColumnMetadata(name="id", data_type="integer", nullable=False),
                        ColumnMetadata(name="email", data_type="text", nullable=True),
                    ]
                ),
            ],
        )
        self.published: AnalyticsCatalog | None = None
        self.published_snapshot_id: UUID | None = None
        self.snapshot_id = uuid4()

    async def get_schema_snapshot(self, _: UUID) -> SchemaSnapshot:
        return self.snapshot

    async def get_schema_snapshot_record(self, _: UUID):
        return SimpleNamespace(id=self.snapshot_id, snapshot=self.snapshot.model_dump(mode="json"))

    async def get_catalog(self, _: UUID):
        if self.published is None:
            return None
        return self.published, "a" * 64, self.published_snapshot_id

    async def publish_catalog(
        self, _: UUID, catalog: AnalyticsCatalog, schema_snapshot_id: UUID
    ) -> str:
        self.published = catalog
        self.published_snapshot_id = schema_snapshot_id
        return "a" * 64


async def test_valid_catalog_publishes_and_becomes_ready() -> None:
    repository = RepositoryStub()
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]

    status = await service.publish(uuid4(), catalog())

    assert repository.published is not None
    assert repository.published_snapshot_id is not None
    assert status.state == "ready"
    assert status.content_hash == "a" * 64


async def test_new_schema_snapshot_marks_catalog_stale_until_republished() -> None:
    repository = RepositoryStub()
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]
    profile_id = uuid4()

    await service.publish(profile_id, catalog())
    assert (await service.status(profile_id)).state == "ready"

    repository.snapshot_id = uuid4()
    assert (await service.status(profile_id)).state == "stale"

    await service.publish(profile_id, catalog())
    assert (await service.status(profile_id)).state == "ready"


async def test_invalid_metric_and_dimension_columns_are_rejected() -> None:
    repository = RepositoryStub()
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]
    invalid = catalog()
    invalid.metrics["total_revenue"].expressions.postgres = "SUM(orders.revenue)"
    invalid.dimensions["order_month"].expressions.postgres = "unknown.month"

    result = await service.validate(uuid4(), invalid)

    assert not result.valid
    assert any("orders.revenue" in error for error in result.errors)
    assert any("unknown.month" in error for error in result.errors)


async def test_undeclared_schema_objects_and_invalid_privacy_are_rejected() -> None:
    repository = RepositoryStub()
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]
    invalid = catalog()
    invalid.join_contracts[0].right = "customers.missing"
    invalid.privacy.privacy_unit = "customers.missing"

    result = await service.validate(uuid4(), invalid)

    assert not result.valid
    assert any("Join column customers.missing" in error for error in result.errors)
    assert any("Privacy unit" in error for error in result.errors)


async def test_unqualified_catalog_references_publish_as_schema_qualified() -> None:
    repository = RepositoryStub()
    repository.snapshot.tables[0].schema_name = "analytics"
    repository.snapshot.tables[1].schema_name = "analytics"
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]

    status = await service.publish(uuid4(), catalog())

    assert status.catalog is not None
    assert status.catalog.metrics["total_revenue"].expressions.postgres == (
        "SUM(analytics.orders.amount)"
    )
    assert status.catalog.privacy.privacy_unit == "analytics.orders.customer_id"


async def test_unqualified_table_is_rejected_when_snapshot_is_ambiguous() -> None:
    repository = RepositoryStub()
    repository.snapshot.tables[0].schema_name = "analytics"
    repository.snapshot.tables.append(
        TableMetadata(
            schema_name="archive", table_name="orders", table_type="table", columns=[
                ColumnMetadata(name="amount", data_type="numeric", nullable=False)
            ]
        )
    )
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]

    result = await service.validate(uuid4(), catalog())

    assert not result.valid
    assert any("orders is ambiguous" in error for error in result.errors)


async def test_sales_reference_is_canonicalized_only_when_schema_is_unique() -> None:
    repository = RepositoryStub()
    repository.snapshot.tables = [
        TableMetadata(
            schema_name="analytics", table_name="sales", table_type="table", columns=[
                ColumnMetadata(name="sales", data_type="numeric", nullable=False),
                ColumnMetadata(name="order_date", data_type="date", nullable=False),
                ColumnMetadata(name="customer_id", data_type="integer", nullable=False),
            ]
        )
    ]
    configured = catalog()
    configured.metrics["total_revenue"].expressions.postgres = "SUM(sales.sales)"
    configured.dimensions["order_month"].expressions.postgres = "sales.order_date"
    configured.join_contracts = []
    configured.column_policies = {}
    configured.privacy.privacy_unit = "sales.customer_id"
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]

    status = await service.publish(uuid4(), configured)

    assert status.catalog is not None
    assert status.catalog.metrics["total_revenue"].expressions.postgres == (
        "SUM(analytics.sales.sales)"
    )


async def test_mysql_unqualified_table_remains_canonical_when_snapshot_has_no_schema() -> None:
    repository = RepositoryStub()
    repository.snapshot.dialect = SQLDialect.MYSQL
    repository.snapshot.tables = [
        TableMetadata(
            schema_name=None, table_name="sales", table_type="table", columns=[
                ColumnMetadata(name="sales", data_type="numeric", nullable=False),
                ColumnMetadata(name="order_date", data_type="date", nullable=False),
                ColumnMetadata(name="customer_id", data_type="integer", nullable=False),
            ]
        )
    ]
    configured = catalog()
    configured.metrics["total_revenue"].expressions.mysql = "SUM(sales.sales)"
    configured.dimensions["order_month"].expressions.mysql = "sales.order_date"
    configured.join_contracts = []
    configured.column_policies = {}
    configured.privacy.privacy_unit = "sales.customer_id"
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]

    status = await service.publish(uuid4(), configured)

    assert status.catalog is not None
    assert status.catalog.metrics["total_revenue"].expressions.mysql == "SUM(sales.sales)"
