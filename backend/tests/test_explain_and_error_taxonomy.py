import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import ExplainResult
from app.infrastructure.databases.base import SQLAlchemyConnectorBase
from app.infrastructure.databases.mysql import MySQLConnector
from app.infrastructure.databases.postgresql import PostgreSQLConnector
from app.infrastructure.sql.cost_guard import CostPolicy, QueryCostGuard


@pytest.mark.parametrize(
    "raw",
    [
        [{"Plan": {"Plan Rows": 2, "Total Cost": 3, "Plans": [{"Plan Rows": 1}]}}],
        '[{"Plan": {"Plan Rows": 2, "Total Cost": 3}}]',
    ],
)
def test_postgres_explain_normalizes_valid_json(raw: object) -> None:
    plan = PostgreSQLConnector._parse_explain(raw)
    assert plan.estimated_rows == 2 and plan.estimated_cost == 3


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not json",
        "[]",
        "{}",
        '[{"Plan": {}}]',
        '[{"Plan": {"Plan Rows": null, "Total Cost": 1}}]',
    ],
)
def test_postgres_explain_malformed_shapes_fail_closed(raw: object) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        PostgreSQLConnector._parse_explain(raw)
    assert captured.value.code is ErrorCode.EXECUTION_FAILED


@pytest.mark.parametrize(
    "raw",
    [
        {"query_block": {"cost_info": {"query_cost": "3"}, "table": {"rows_produced_per_join": 2}}},
        (
            '{"query_block": {"cost_info": {"query_cost": "3"}, '
            '"nested_loop": [{"table": {"rows_examined_per_scan": 2}}]}}'
        ),
    ],
)
def test_mysql_explain_normalizes_supported_shapes(raw: object) -> None:
    plan = MySQLConnector._parse_explain(raw)
    assert plan.estimated_cost == 3 and plan.estimated_rows == 2


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "bad",
        "{}",
        '{"query_block": {}}',
        '{"query_block": {"cost_info": {"query_cost": null}}}',
    ],
)
def test_mysql_explain_malformed_shapes_fail_closed(raw: object) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        MySQLConnector._parse_explain(raw)
    assert captured.value.code is ErrorCode.EXECUTION_FAILED


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("statement timeout", ErrorCode.QUERY_TIMEOUT),
        ("Lock wait timeout exceeded", ErrorCode.LOCK_TIMEOUT),
        ("deadlock found", ErrorCode.LOCK_TIMEOUT),
        ("connection refused", ErrorCode.DATABASE_UNAVAILABLE),
    ],
)
def test_database_error_normalization(message: str, code: ErrorCode) -> None:
    assert SQLAlchemyConnectorBase._normalize_error(SQLAlchemyError(message)).code is code


def test_cost_error_category() -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        QueryCostGuard().validate(
            ExplainResult(raw_plan={}, estimated_rows=2, estimated_cost=2), CostPolicy(1, 1)
        )
    assert captured.value.code is ErrorCode.QUERY_TOO_EXPENSIVE
