import json
from hashlib import sha256
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
                    "descriptions": {"en": "Recognized revenue", "ar": "الإيرادات"},
                    "expressions": {
                        "postgres": "SUM(orders.amount)",
                        "mysql": "SUM(orders.amount)",
                    },
                }
            },
            "dimensions": {
                "order_month": {
                    "labels": {"en": "Order month", "ar": "شهر الطلب"},
                    "aliases": {"en": ["month"], "ar": ["الشهر"]},
                    "descriptions": {"en": "Order date", "ar": "تاريخ الطلب"},
                    "expressions": {
                        "postgres": "orders.order_date",
                        "mysql": "orders.order_date",
                    },
                }
            },
        }
    )


class RepositoryStub:
    def __init__(self) -> None:
        self.snapshot = SchemaSnapshot(
            dialect=SQLDialect.POSTGRES,
            database_name="sales",
            server_version="16",
            capabilities=DatabaseCapabilities(
                dialect=SQLDialect.POSTGRES, server_version="16"
            ),
            tables=[
                TableMetadata(
                    schema_name=None,
                    table_name="orders",
                    table_type="table",
                    columns=[
                        ColumnMetadata(name="id", data_type="integer", nullable=False),
                        ColumnMetadata(name="amount", data_type="numeric", nullable=False),
                        ColumnMetadata(name="order_date", data_type="date", nullable=False),
                    ],
                )
            ],
        )
        self.published: AnalyticsCatalog | None = None
        self.published_snapshot_id: UUID | None = None
        self.publish_calls = 0
        self.snapshot_id = uuid4()

    async def get_schema_snapshot_record(self, _: UUID):
        return SimpleNamespace(
            id=self.snapshot_id, snapshot=self.snapshot.model_dump(mode="json")
        )

    async def get_catalog(self, _: UUID):
        if self.published is None:
            return None
        return self.published, "a" * 64, self.published_snapshot_id

    async def publish_catalog(
        self, _: UUID, catalog: AnalyticsCatalog, schema_snapshot_id: UUID
    ) -> str:
        self.publish_calls += 1
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


async def test_status_returns_published_catalog_and_detects_stale_snapshot() -> None:
    repository = RepositoryStub()
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]
    profile_id = uuid4()

    published = await service.publish(profile_id, catalog())
    loaded = await service.status(profile_id)
    assert repository.publish_calls == 1
    assert loaded.catalog == published.catalog
    assert loaded.state == "ready"

    repository.snapshot_id = uuid4()
    assert (await service.status(profile_id)).state == "stale"


async def test_invalid_preferred_metric_and_dimension_columns_are_rejected() -> None:
    repository = RepositoryStub()
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]
    invalid = catalog()
    invalid.metrics["total_revenue"].expressions.postgres = "SUM(orders.revenue)"
    invalid.dimensions["order_month"].expressions.postgres = "unknown.month"

    result = await service.validate(uuid4(), invalid)

    assert not result.valid
    assert any("orders.revenue" in error for error in result.errors)
    assert any("unknown.month" in error for error in result.errors)


async def test_unqualified_business_definitions_publish_as_schema_qualified() -> None:
    repository = RepositoryStub()
    repository.snapshot.tables[0].schema_name = "analytics"
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]

    status = await service.publish(uuid4(), catalog())

    assert status.catalog is not None
    assert status.catalog.metrics["total_revenue"].expressions.postgres == (
        "SUM(analytics.orders.amount)"
    )
    assert status.catalog.dimensions["order_month"].expressions.postgres == (
        "analytics.orders.order_date"
    )


async def test_unqualified_table_is_rejected_when_snapshot_is_ambiguous() -> None:
    repository = RepositoryStub()
    repository.snapshot.tables[0].schema_name = "analytics"
    repository.snapshot.tables.append(
        TableMetadata(
            schema_name="archive",
            table_name="orders",
            table_type="table",
            columns=[ColumnMetadata(name="amount", data_type="numeric", nullable=False)],
        )
    )
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]

    result = await service.validate(uuid4(), catalog())

    assert not result.valid
    assert any("orders is ambiguous" in error for error in result.errors)


async def test_equivalent_business_definitions_have_same_canonical_content() -> None:
    repository = RepositoryStub()
    repository.snapshot.tables[0].schema_name = "analytics"
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]
    unqualified, qualified = catalog(), catalog()
    qualified.metrics["total_revenue"].expressions.postgres = (
        "SUM(analytics.orders.amount)"
    )
    qualified.metrics["total_revenue"].expressions.mysql = (
        "SUM(analytics.orders.amount)"
    )
    qualified.dimensions["order_month"].expressions.postgres = (
        "analytics.orders.order_date"
    )
    qualified.dimensions["order_month"].expressions.mysql = (
        "analytics.orders.order_date"
    )

    first = await service.publish(uuid4(), unqualified)
    second = await service.publish(uuid4(), qualified)

    assert first.catalog is not None and second.catalog is not None
    first_content = first.catalog.model_dump(mode="json")
    second_content = second.catalog.model_dump(mode="json")
    assert first_content == second_content
    assert sha256(
        json.dumps(first_content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest() == sha256(
        json.dumps(second_content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def test_mysql_unqualified_table_remains_unqualified_without_schema() -> None:
    repository = RepositoryStub()
    repository.snapshot.dialect = SQLDialect.MYSQL
    configured = catalog()
    service = CatalogConfigurationService(repository)  # type: ignore[arg-type]

    status = await service.publish(uuid4(), configured)

    assert status.catalog is not None
    assert status.catalog.metrics["total_revenue"].expressions.mysql == "SUM(orders.amount)"
