import os
from uuid import UUID

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.connection_profiles import (
    ConnectionProfileInput,
    ConnectionProfileView,
    ConnectionTestResult,
    SchemaRefreshResult,
    SetupProgress,
)
from app.domain.databases.connector import SQLDatabaseConnector
from app.infrastructure.databases.factory import create_database_connector
from app.persistence.repositories import ConnectionProfileRepository


class ConnectionSetupService:
    def __init__(self, repository: ConnectionProfileRepository) -> None:
        self._repository = repository

    @staticmethod
    def _connector(input: ConnectionProfileInput) -> SQLDatabaseConnector:
        database_url = os.environ.get(input.credential_reference)
        if not database_url:
            raise Prompt2InsightError(
                ErrorCode.NOT_CONFIGURED,
                "The credential environment variable is unavailable.",
            )
        return create_database_connector(
            dialect=input.dialect, database_url=database_url, approved_schemas=()
        )

    async def list(self) -> list[ConnectionProfileView]:
        return await self._repository.list()

    async def test(self, input: ConnectionProfileInput) -> ConnectionTestResult:
        try:
            connector = self._connector(input)
        except Prompt2InsightError as error:
            return ConnectionTestResult(
                status="failed", code=error.code.value, message=error.message
            )
        try:
            await connector.test_connection()
        except Prompt2InsightError as error:
            return ConnectionTestResult(
                status="failed", code=error.code.value, message=error.message
            )
        finally:
            await connector.close()
        return ConnectionTestResult(status="success", message="Connection successful")

    async def save_and_introspect(self, input: ConnectionProfileInput) -> SetupProgress:
        result = await self.test(input)
        if result.status != "success":
            raise Prompt2InsightError(ErrorCode.DATABASE_UNAVAILABLE, result.message)
        profile = await self._repository.create(input)
        connector = self._connector(input)
        try:
            snapshot = await connector.get_schema_snapshot()
        except Prompt2InsightError as error:
            raise Prompt2InsightError(
                ErrorCode.SCHEMA_INTROSPECTION_FAILED, "Could not read the database schema."
            ) from error
        finally:
            await connector.close()
        await self._repository.save_snapshot(profile.id, snapshot)
        return SetupProgress(
            profile=profile.model_copy(update={"state": "catalog_needs_configuration"}),
            schema_state="ready",
            catalog_state="catalog_needs_configuration",
        )

    async def select(self, profile_id: UUID) -> SetupProgress:
        profiles = await self.list()
        profile = next((item for item in profiles if item.id == profile_id), None)
        if profile is None:
            raise Prompt2InsightError(
                ErrorCode.NOT_CONFIGURED, "Connection profile is unavailable."
            )
        if profile.state != "ready":
            return SetupProgress(profile=profile, schema_state="ready", catalog_state=profile.state)
        return SetupProgress(
            profile=profile,
            schema_state="ready",
            catalog_state="ready",
        )

    async def refresh_schema(self, profile_id: UUID) -> SchemaRefreshResult:
        profile = await self._repository.get_input(profile_id)
        if profile is None:
            raise Prompt2InsightError(
                ErrorCode.NOT_CONFIGURED, "Connection profile is unavailable."
            )
        connector = self._connector(profile)
        try:
            await connector.test_connection()
            try:
                snapshot = await connector.get_schema_snapshot()
            except Prompt2InsightError as error:
                raise Prompt2InsightError(
                    ErrorCode.SCHEMA_INTROSPECTION_FAILED,
                    "Could not read the database schema.",
                ) from error
        finally:
            await connector.close()
        snapshot_record, schema_changed = await self._repository.refresh_snapshot(
            profile_id, snapshot
        )
        return SchemaRefreshResult(
            profile_id=profile_id,
            schema_snapshot_id=snapshot_record.id,
            schema_changed=schema_changed,
            state=await self._repository.state_for_snapshot(profile_id, snapshot_record.id),
        )
