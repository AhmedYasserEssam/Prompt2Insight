import os

from app.application.analytics.run_analytics_request import PlanningContext
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.connector import SQLDatabaseConnector
from app.infrastructure.databases.factory import create_database_connector


class EnvironmentConnectorResolver:
    """Resolves the profile's secret-reference name without persisting credentials."""

    async def connect(self, context: PlanningContext) -> SQLDatabaseConnector:
        if context.credential_reference is None:
            raise Prompt2InsightError(
                ErrorCode.NOT_CONFIGURED, "No analytics credential is configured."
            )
        database_url = os.environ.get(context.credential_reference)
        if not database_url:
            raise Prompt2InsightError(
                ErrorCode.NOT_CONFIGURED, "Analytics credential is unavailable."
            )
        schemas = tuple(
            sorted(
                {table.schema_name for table in context.schema_snapshot.tables if table.schema_name}
            )
        )
        return create_database_connector(
            dialect=context.dialect, database_url=database_url, approved_schemas=schemas
        )
