import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect
from app.infrastructure.sql.validator import SQLPolicy, SQLValidator


@pytest.fixture
def policy() -> SQLPolicy:
    return SQLPolicy(
        allowed_tables=frozenset({"analytics.orders"}),
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


def test_postgres_normalization_preserves_named_sqlalchemy_bind_parameters(
    policy: SQLPolicy,
) -> None:
    result = SQLValidator().validate(
        sql=(
            "SELECT '%(region_value)s' AS marker, region FROM analytics.orders "
            "WHERE region = :region_value"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=policy,
    )

    assert ":region_value" in result.normalized_sql
    assert "'%(region_value)s'" in result.normalized_sql
    compiled = text(result.normalized_sql).compile(dialect=PGDialect_asyncpg())
    assert compiled.params == {"region_value": None}


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


@pytest.fixture
def sales_policy() -> SQLPolicy:
    return SQLPolicy(
        allowed_tables=frozenset({"analytics.sales"}),
        allowed_columns=frozenset(
            {
                "analytics.sales.order_date",
                "analytics.sales.sales",
                "analytics.sales.region",
                "analytics.sales.category",
                "analytics.sales.customer_name",
            }
        ),
        maximum_rows=100,
    )


def test_order_by_projection_alias_authorizes_only_the_underlying_column(
    sales_policy: SQLPolicy,
) -> None:
    result = SQLValidator().validate(
        sql="SELECT analytics.sales.order_date AS d FROM analytics.sales ORDER BY d",
        dialect=SQLDialect.POSTGRES,
        policy=sales_policy,
    )

    assert result.referenced_columns == frozenset({"analytics.sales.order_date"})


def test_monthly_sales_order_by_output_alias_is_not_a_physical_column(
    sales_policy: SQLPolicy,
) -> None:
    result = SQLValidator().validate(
        sql=(
            "SELECT DATE_TRUNC('MONTH', analytics.sales.order_date) AS order_month, "
            "SUM(analytics.sales.sales) AS total_sales FROM analytics.sales "
            "GROUP BY DATE_TRUNC('MONTH', analytics.sales.order_date) ORDER BY order_month"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=sales_policy,
    )

    assert result.referenced_columns == frozenset(
        {"analytics.sales.order_date", "analytics.sales.sales"}
    )


def test_monthly_sales_group_by_output_alias_is_not_a_physical_column(
    sales_policy: SQLPolicy,
) -> None:
    result = SQLValidator().validate(
        sql=(
            "SELECT DATE_TRUNC('MONTH', analytics.sales.order_date) AS order_month, "
            "SUM(analytics.sales.sales) AS total_sales FROM analytics.sales "
            "GROUP BY order_month ORDER BY order_month"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=sales_policy,
    )

    assert result.referenced_columns == frozenset(
        {"analytics.sales.order_date", "analytics.sales.sales"}
    )


def test_group_by_simple_projection_alias_is_not_a_physical_column(
    sales_policy: SQLPolicy,
) -> None:
    result = SQLValidator().validate(
        sql="SELECT analytics.sales.region AS r FROM analytics.sales GROUP BY r",
        dialect=SQLDialect.POSTGRES,
        policy=sales_policy,
    )

    assert result.referenced_columns == frozenset({"analytics.sales.region"})


def test_mysql_group_by_simple_projection_alias_is_not_a_physical_column(
    sales_policy: SQLPolicy,
) -> None:
    result = SQLValidator().validate(
        sql="SELECT analytics.sales.region AS r FROM analytics.sales GROUP BY r",
        dialect=SQLDialect.MYSQL,
        policy=sales_policy,
    )

    assert result.referenced_columns == frozenset({"analytics.sales.region"})


def test_group_by_unknown_alias_remains_an_unauthorized_column(sales_policy: SQLPolicy) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql="SELECT analytics.sales.region FROM analytics.sales GROUP BY unknown_alias",
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_duplicate_group_by_alias_fails_closed(sales_policy: SQLPolicy) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql=(
                "SELECT analytics.sales.region AS value, analytics.sales.category AS value "
                "FROM analytics.sales GROUP BY value"
            ),
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_projection_alias_is_not_authorized_in_where(sales_policy: SQLPolicy) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql=(
                "SELECT analytics.sales.region AS r FROM analytics.sales "
                "WHERE r = 'West'"
            ),
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_projection_alias_is_not_authorized_in_having(sales_policy: SQLPolicy) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql=(
                "SELECT analytics.sales.region AS r FROM analytics.sales "
                "GROUP BY analytics.sales.region HAVING r = 'West'"
            ),
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_projection_alias_is_not_authorized_in_a_window_clause(
    sales_policy: SQLPolicy,
) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql=(
                "SELECT SUM(analytics.sales.sales) OVER (PARTITION BY r), "
                "analytics.sales.region AS r FROM analytics.sales"
            ),
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_inner_projection_alias_does_not_authorize_an_outer_reference(
    sales_policy: SQLPolicy,
) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql=(
                "SELECT analytics.sales.order_date FROM analytics.sales "
                "WHERE order_month = (SELECT analytics.sales.order_date AS order_month "
                "FROM analytics.sales)"
            ),
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_group_by_alias_preserves_approved_underlying_column(
    sales_policy: SQLPolicy,
) -> None:
    result = SQLValidator().validate(
        sql=(
            "SELECT analytics.sales.customer_name AS customer FROM analytics.sales "
            "GROUP BY customer"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=sales_policy,
    )

    assert result.referenced_columns == frozenset({"analytics.sales.customer_name"})


def test_unknown_order_by_alias_remains_an_unauthorized_column(sales_policy: SQLPolicy) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql="SELECT analytics.sales.order_date AS d FROM analytics.sales ORDER BY unknown",
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_order_alias_preserves_approved_underlying_column(sales_policy: SQLPolicy) -> None:
    result = SQLValidator().validate(
        sql=(
            "SELECT analytics.sales.customer_name AS customer FROM analytics.sales "
            "ORDER BY customer"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=sales_policy,
    )

    assert result.referenced_columns == frozenset({"analytics.sales.customer_name"})


def test_duplicate_order_by_alias_fails_closed(sales_policy: SQLPolicy) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql=(
                "SELECT analytics.sales.order_date AS value, analytics.sales.sales AS value "
                "FROM analytics.sales ORDER BY value"
            ),
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_projection_alias_is_not_authorized_in_an_unrelated_nested_scope(
    sales_policy: SQLPolicy,
) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql=(
                "SELECT analytics.sales.order_date AS d FROM analytics.sales "
                "WHERE d = (SELECT d) ORDER BY d"
            ),
            dialect=SQLDialect.POSTGRES,
            policy=sales_policy,
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_COLUMN


def test_cte_derived_output_remains_distinct_from_physical_columns(sales_policy: SQLPolicy) -> None:
    result = SQLValidator().validate(
        sql=(
            "WITH monthly AS (SELECT analytics.sales.order_date AS order_month "
            "FROM analytics.sales) SELECT monthly.order_month FROM monthly"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=sales_policy,
    )

    assert result.referenced_columns == frozenset({"analytics.sales.order_date"})
