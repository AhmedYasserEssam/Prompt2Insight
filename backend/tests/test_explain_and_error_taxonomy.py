import pytest
from sqlalchemy.exc import DataError, SQLAlchemyError

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
    ("message", "code", "retryable"),
    [
        ("statement timeout", ErrorCode.QUERY_TIMEOUT, False),
        ("Lock wait timeout exceeded", ErrorCode.LOCK_TIMEOUT, False),
        ("deadlock found", ErrorCode.LOCK_TIMEOUT, False),
        ("password authentication failed", ErrorCode.AUTHENTICATION_FAILED, False),
        ("permission denied for table orders", ErrorCode.SQL_POLICY_REJECTED, False),
        ("connection refused", ErrorCode.DATABASE_UNAVAILABLE, True),
        ("server closed connection unexpectedly", ErrorCode.DATABASE_UNAVAILABLE, True),
        ("operator does not exist: date >= text", ErrorCode.QUERY_EXECUTION_FAILED, False),
    ],
)
def test_database_error_normalization(
    message: str, code: ErrorCode, retryable: bool
) -> None:
    error = SQLAlchemyConnectorBase._normalize_error(SQLAlchemyError(message))

    assert error.code is code
    assert error.retryable is retryable


def test_data_error_is_query_execution_failure_not_database_unavailable() -> None:
    error = DataError(
        "SELECT :start_date",
        {"start_date": "2015-01-01"},
        ValueError("invalid input for query argument $1: str object has no attribute toordinal"),
    )

    normalized = SQLAlchemyConnectorBase._normalize_error(error)

    assert normalized.code is ErrorCode.QUERY_EXECUTION_FAILED
    assert not normalized.retryable
    assert normalized.safe_detail == (
        "invalid input for query argument $1: str object has no attribute toordinal"
    )


def test_cost_error_category() -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        QueryCostGuard().validate(
            ExplainResult(raw_plan={}, estimated_rows=2, estimated_cost=2), CostPolicy(1, 1)
        )
    assert captured.value.code is ErrorCode.QUERY_TOO_EXPENSIVE
