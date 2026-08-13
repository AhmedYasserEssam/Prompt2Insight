from uuid import uuid4

import pytest

from app.application.databases.setup_connection import ConnectionSetupService
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.connection_profiles import ConnectionProfileInput, ConnectionProfileView
from app.domain.databases.models import (
    DatabaseCapabilities,
    SchemaSnapshot,
    SQLDialect,
)


class RepositoryStub:
    def __init__(self) -> None:
        self.profile = ConnectionProfileView(
            id=uuid4(), name="Sales", dialect=SQLDialect.POSTGRES, host="db", port=5432,
            database_name="sales", username="reader", credential_reference="SALES_DATABASE_URL",
            state="draft",
        )
        self.snapshot: SchemaSnapshot | None = None

    async def list(self) -> list[ConnectionProfileView]:
        return [self.profile]

    async def create(self, _: ConnectionProfileInput) -> ConnectionProfileView:
        return self.profile

    async def save_snapshot(self, _: object, snapshot: SchemaSnapshot) -> None:
        self.snapshot = snapshot

    async def create_conversation(self, _: object):
        return uuid4()


class ConnectorStub:
    async def test_connection(self) -> None:
        return None

    async def get_schema_snapshot(self) -> SchemaSnapshot:
        return SchemaSnapshot(
            dialect=SQLDialect.POSTGRES, database_name="sales", server_version="16",
            tables=[],
            capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
        )

    async def close(self) -> None:
        return None


@pytest.fixture
def input() -> ConnectionProfileInput:
    return ConnectionProfileInput(
        name="Sales", dialect=SQLDialect.POSTGRES, host="db", port=5432, database_name="sales",
        username="reader", credential_reference="SALES_DATABASE_URL",
    )


async def test_connection_test_returns_normalized_authentication_failure(
    monkeypatch: pytest.MonkeyPatch, input: ConnectionProfileInput
) -> None:
    service = ConnectionSetupService(RepositoryStub())  # type: ignore[arg-type]

    def unavailable(_: ConnectionProfileInput) -> ConnectorStub:
        raise Prompt2InsightError(ErrorCode.AUTHENTICATION_FAILED, "Could not authenticate")

    monkeypatch.setattr(service, "_connector", unavailable)
    result = await service.test(input)

    assert result.status == "failed"
    assert result.code == "authentication_failed"


async def test_save_introspects_schema_after_successful_connection(
    monkeypatch: pytest.MonkeyPatch, input: ConnectionProfileInput
) -> None:
    repository = RepositoryStub()
    service = ConnectionSetupService(repository)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_connector", lambda _: ConnectorStub())

    progress = await service.save_and_introspect(input)

    assert repository.snapshot is not None
    assert progress.schema_state == "ready"
    assert progress.catalog_state == "catalog_needs_configuration"
