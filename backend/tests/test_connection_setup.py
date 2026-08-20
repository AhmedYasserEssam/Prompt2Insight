from uuid import uuid4

import pytest

from app.application.databases.setup_connection import ConnectionSetupService
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.connection_profiles import (
    ConnectionProfileInput,
    ConnectionProfileView,
)
from app.domain.databases.models import (
    DatabaseCapabilities,
    SchemaSnapshot,
    SQLDialect,
    TableMetadata,
)


class RepositoryStub:
    def __init__(self) -> None:
        self.profile = ConnectionProfileView(
            id=uuid4(), name="Sales", dialect=SQLDialect.POSTGRES, host="db", port=5432,
            database_name="sales", username="reader", credential_reference="SALES_DATABASE_URL",
            state="draft",
        )
        self.snapshot: SchemaSnapshot | None = None
        self.snapshot_id = uuid4()
        self.catalog_snapshot_id = self.snapshot_id
        self.refresh_calls = 0

    async def list(self) -> list[ConnectionProfileView]:
        return [self.profile]

    async def create(self, _: ConnectionProfileInput) -> ConnectionProfileView:
        return self.profile

    async def save_snapshot(self, _: object, snapshot: SchemaSnapshot) -> None:
        self.snapshot = snapshot

    async def get_input(self, _: object) -> ConnectionProfileInput:
        return ConnectionProfileInput(
            name=self.profile.name,
            dialect=self.profile.dialect,
            host=self.profile.host,
            port=self.profile.port,
            database_name=self.profile.database_name,
            username=self.profile.username,
            credential_reference=self.profile.credential_reference,
        )

    async def refresh_snapshot(self, _: object, snapshot: SchemaSnapshot):
        changed = self.snapshot is None or self.snapshot.fingerprint() != snapshot.fingerprint()
        if changed:
            self.snapshot = snapshot
            self.snapshot_id = uuid4()
            self.refresh_calls += 1
        from types import SimpleNamespace

        return SimpleNamespace(id=self.snapshot_id), changed

    async def state_for_snapshot(self, _: object, snapshot_id: object) -> str:
        return "ready" if snapshot_id == self.catalog_snapshot_id else "stale"


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


async def test_selecting_ready_connection_does_not_create_conversation() -> None:
    repository = RepositoryStub()
    repository.profile = repository.profile.model_copy(update={"state": "ready"})
    service = ConnectionSetupService(repository)  # type: ignore[arg-type]

    progress = await service.select(repository.profile.id)

    assert progress.catalog_state == "ready"
    assert progress.conversation_id is None


async def test_refresh_changed_schema_creates_new_snapshot_and_marks_catalog_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RepositoryStub()
    old_snapshot = await ConnectorStub().get_schema_snapshot()
    repository.snapshot = old_snapshot
    old_snapshot_id = repository.snapshot_id
    refreshed = old_snapshot.model_copy(
        update={
            "tables": [
                *old_snapshot.tables,
                TableMetadata(
                    schema_name="analytics",
                    table_name="sales",
                    table_type="table",
                    columns=[],
                )
            ]
        }
    )
    service = ConnectionSetupService(repository)  # type: ignore[arg-type]
    connector = ConnectorStub()
    monkeypatch.setattr(service, "_connector", lambda _: connector)
    monkeypatch.setattr(connector, "get_schema_snapshot", lambda: _snapshot(refreshed))

    result = await service.refresh_schema(repository.profile.id)

    assert result.schema_changed
    assert result.state == "stale"
    assert result.schema_snapshot_id != old_snapshot_id
    assert repository.snapshot == refreshed
    assert repository.catalog_snapshot_id == old_snapshot_id


async def test_refresh_unchanged_schema_reuses_snapshot_and_keeps_profile_ready(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = RepositoryStub()
    repository.snapshot = await ConnectorStub().get_schema_snapshot()
    service = ConnectionSetupService(repository)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_connector", lambda _: ConnectorStub())

    result = await service.refresh_schema(repository.profile.id)

    assert not result.schema_changed
    assert result.state == "ready"
    assert repository.refresh_calls == 0


async def test_refresh_preserves_postgres_schema_name(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = RepositoryStub()
    snapshot = SchemaSnapshot(
        dialect=SQLDialect.POSTGRES,
        database_name="analytics",
        server_version="16",
        tables=[
            TableMetadata(
                schema_name="analytics", table_name="sales", table_type="table", columns=[]
            )
        ],
        capabilities=DatabaseCapabilities(dialect=SQLDialect.POSTGRES, server_version="16"),
    )
    service = ConnectionSetupService(repository)  # type: ignore[arg-type]
    connector = ConnectorStub()
    monkeypatch.setattr(service, "_connector", lambda _: connector)
    monkeypatch.setattr(connector, "get_schema_snapshot", lambda: _snapshot(snapshot))

    await service.refresh_schema(repository.profile.id)

    assert repository.snapshot is not None
    assert repository.snapshot.tables[0].schema_name == "analytics"
    assert repository.snapshot.tables[0].table_name == "sales"


async def _snapshot(snapshot: SchemaSnapshot) -> SchemaSnapshot:
    return snapshot
