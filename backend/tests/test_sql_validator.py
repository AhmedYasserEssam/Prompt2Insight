import pytest

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect
from app.infrastructure.sql.validator import SQLPolicy, SQLValidator


@pytest.fixture
def policy() -> SQLPolicy:
    return SQLPolicy(
        allowed_tables=frozenset({"analytics.orders"}),
        prohibited_columns=frozenset({"password_hash"}),
        maximum_rows=100,
    )


def test_accepts_safe_select(policy: SQLPolicy) -> None:
    result = SQLValidator().validate(
        sql="SELECT region, COUNT(id) AS total FROM analytics.orders GROUP BY region",
        dialect=SQLDialect.POSTGRES,
        policy=policy,
    )
    assert "LIMIT 100" in result.normalized_sql
    assert result.referenced_tables == frozenset({"analytics.orders"})


def test_rejects_multiple_statements(policy: SQLPolicy) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql="SELECT id FROM analytics.orders; DELETE FROM analytics.orders",
            dialect=SQLDialect.POSTGRES,
            policy=policy,
        )
    assert captured.value.code is ErrorCode.SQL_POLICY_REJECTED


def test_rejects_select_star(policy: SQLPolicy) -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(
            sql="SELECT * FROM analytics.orders",
            dialect=SQLDialect.MYSQL,
            policy=policy,
        )
