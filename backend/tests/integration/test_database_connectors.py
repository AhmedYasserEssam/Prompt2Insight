import os

import pytest

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import PreparedQuery, SQLDialect
from app.infrastructure.databases.factory import create_database_connector
from app.infrastructure.databases.postgresql import PostgreSQLConnector

pytestmark = pytest.mark.skipif(
    os.getenv("P2I_RUN_INTEGRATION") != "1",
    reason="set P2I_RUN_INTEGRATION=1 to run database connector contract tests",
)


CONNECTORS = (
    (SQLDialect.POSTGRES, "P2I_POSTGRES_ANALYTICS_URL", "SELECT pg_sleep(1)"),
    (
        SQLDialect.MYSQL,
        "P2I_MYSQL_ANALYTICS_URL",
        "SELECT SLEEP(1)",
    ),
)


@pytest.fixture(params=CONNECTORS, ids=lambda item: item[0].value)
async def connector(request: pytest.FixtureRequest):
    dialect, url_variable, _ = request.param
    database_url = os.environ[url_variable]
    instance = create_database_connector(
        dialect=dialect,
        database_url=database_url,
        approved_schemas=("analytics",),
    )
    try:
        yield instance
    finally:
        await instance.close()


async def test_analytics_role_can_connect_and_introspect_visible_schema(connector) -> None:
    await connector.test_connection()

    schema = await connector.get_schema_snapshot()

    assert schema.dialect is connector.dialect
    assert any(table.table_name == "orders" for table in schema.tables)
    if connector.dialect is SQLDialect.POSTGRES:
        assert any(
            table.schema_name == "analytics" and table.table_name == "orders"
            for table in schema.tables
        )


async def test_postgres_introspection_discovers_actual_namespace_without_approved_schemas() -> None:
    connector = PostgreSQLConnector(os.environ["P2I_POSTGRES_ANALYTICS_URL"], approved_schemas=())
    try:
        snapshot = await connector.get_schema_snapshot()
    finally:
        await connector.close()

    assert any(
        table.schema_name == "analytics" and table.table_name == "orders"
        for table in snapshot.tables
    )


async def test_connector_returns_json_explain_plan(connector) -> None:
    plan = await connector.explain(PreparedQuery(sql="SELECT id FROM analytics.orders"))

    assert plan.raw_plan


async def test_analytics_role_cannot_execute_dml_or_ddl(connector) -> None:
    for statement in (
        "INSERT INTO analytics.orders (id, total) VALUES (999, 10)",
        "CREATE TABLE analytics.forbidden (id integer)",
    ):
        with pytest.raises(Prompt2InsightError) as captured:
            await connector.execute_read_only(PreparedQuery(sql=statement))
        assert captured.value.code is ErrorCode.SQL_POLICY_REJECTED


async def test_long_query_is_terminated_as_a_timeout(connector, request) -> None:
    _, _, slow_query = request.node.callspec.params["connector"]

    with pytest.raises(Prompt2InsightError) as captured:
        await connector.execute_read_only(
            PreparedQuery(sql=slow_query, timeout_ms=100, maximum_rows=1)
        )

    assert captured.value.code is ErrorCode.QUERY_TIMEOUT
