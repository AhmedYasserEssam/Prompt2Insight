import pytest

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect
from app.infrastructure.sql.validator import SQLPolicy, SQLValidator

POLICY = SQLPolicy(
    allowed_tables=frozenset(
        {"analytics.orders", "analytics.order_items", "analytics.customers"}
    ),
    allowed_columns=frozenset(
        {
            "analytics.orders.id",
            "analytics.orders.region",
            "analytics.orders.customer_id",
            "analytics.order_items.order_id",
            "analytics.order_items.net_amount",
            "analytics.customers.id",
            "analytics.customers.email",
            "analytics.customers.phone",
        }
    ),
    maximum_rows=10,
)


@pytest.mark.parametrize("dialect", [SQLDialect.POSTGRES, SQLDialect.MYSQL])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT analytics.orders.id FROM analytics.orders",
        "WITH x AS (SELECT analytics.orders.id FROM analytics.orders) SELECT x.id FROM x",
    ],
)
def test_safe_selects_are_allowed(dialect: SQLDialect, sql: str) -> None:
    assert SQLValidator().validate(sql=sql, dialect=dialect, policy=POLICY).normalized_sql


@pytest.mark.parametrize("dialect", [SQLDialect.POSTGRES, SQLDialect.MYSQL])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT analytics.orders.id FROM analytics.orders; SELECT 1",
        "INSERT INTO analytics.orders VALUES (1)",
        "UPDATE analytics.orders SET id = 2",
        "DELETE FROM analytics.orders",
        "DROP TABLE analytics.orders",
        "ALTER TABLE analytics.orders ADD x INT",
        "CREATE TABLE analytics.x (id INT)",
        "TRUNCATE TABLE analytics.orders",
        "WITH x AS (DELETE FROM analytics.orders RETURNING id) SELECT id FROM x",
    ],
)
def test_mutation_and_multiple_statements_are_rejected(
    dialect: SQLDialect, sql: str
) -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(sql=sql, dialect=dialect, policy=POLICY)


@pytest.mark.parametrize(
    "dialect, sql",
    [
        (SQLDialect.POSTGRES, "SELECT analytics.orders.id INTO new_table FROM analytics.orders"),
        (SQLDialect.POSTGRES, "SELECT analytics.orders.id FROM analytics.orders FOR UPDATE"),
        (SQLDialect.POSTGRES, "SELECT analytics.orders.id FROM analytics.orders FOR SHARE"),
        (
            SQLDialect.MYSQL,
            "SELECT analytics.orders.id FROM analytics.orders INTO OUTFILE '/tmp/x'",
        ),
        (
            SQLDialect.MYSQL,
            "SELECT analytics.orders.id FROM analytics.orders INTO DUMPFILE '/tmp/x'",
        ),
    ],
)
def test_dangerous_select_forms_are_rejected(dialect: SQLDialect, sql: str) -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(sql=sql, dialect=dialect, policy=POLICY)


@pytest.mark.parametrize(
    "dialect, sql, code",
    [
        (
            SQLDialect.POSTGRES,
            "SELECT id FROM pg_catalog.pg_tables",
            ErrorCode.UNAUTHORIZED_TABLE,
        ),
        (
            SQLDialect.POSTGRES,
            "SELECT table_name FROM information_schema.tables",
            ErrorCode.UNAUTHORIZED_TABLE,
        ),
        (SQLDialect.MYSQL, "SELECT id FROM mysql.user", ErrorCode.UNAUTHORIZED_TABLE),
        (
            SQLDialect.POSTGRES,
            "SELECT analytics.orders.fake FROM analytics.orders",
            ErrorCode.UNAUTHORIZED_COLUMN,
        ),
        (SQLDialect.POSTGRES, "SELECT * FROM analytics.orders", ErrorCode.SQL_POLICY_REJECTED),
        (
            SQLDialect.POSTGRES,
            "SELECT pg_sleep(1) FROM analytics.orders",
            ErrorCode.SQL_POLICY_REJECTED,
        ),
        (
            SQLDialect.POSTGRES,
            "SELECT pg_read_file('/etc/passwd') FROM analytics.orders",
            ErrorCode.SQL_POLICY_REJECTED,
        ),
        (
            SQLDialect.POSTGRES,
            "SELECT set_config('x', 'y', false) FROM analytics.orders",
            ErrorCode.SQL_POLICY_REJECTED,
        ),
        (
            SQLDialect.MYSQL,
            "SELECT load_file('/etc/passwd') FROM analytics.orders",
            ErrorCode.SQL_POLICY_REJECTED,
        ),
    ],
)
def test_physical_access_and_dangerous_functions_fail_closed(
    dialect: SQLDialect, sql: str, code: ErrorCode
) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(sql=sql, dialect=dialect, policy=POLICY)
    assert captured.value.code is code


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT AVG(analytics.order_items.net_amount) FROM analytics.order_items",
        "SELECT MIN(analytics.order_items.net_amount), "
        "MAX(analytics.order_items.net_amount) FROM analytics.order_items",
        "SELECT COUNT(DISTINCT analytics.orders.customer_id) FROM analytics.orders",
        "SELECT SUM(analytics.order_items.net_amount) / "
        "NULLIF(COUNT(analytics.order_items.order_id), 0) FROM analytics.order_items",
        "SELECT DATE_TRUNC('month', analytics.orders.id), "
        "EXTRACT(YEAR FROM analytics.orders.id) FROM analytics.orders",
        "SELECT CASE WHEN analytics.orders.region IS NULL THEN 'unknown' "
        "ELSE analytics.orders.region END FROM analytics.orders",
        "SELECT arbitrary_analytics_udf(analytics.orders.id) FROM analytics.orders",
    ],
)
def test_normal_and_derived_analytics_functions_are_allowed(sql: str) -> None:
    SQLValidator().validate(sql=sql, dialect=SQLDialect.POSTGRES, policy=POLICY)


def test_valid_equality_join_requires_no_semantic_contract() -> None:
    result = SQLValidator().validate(
        sql=(
            "SELECT o.id, c.email FROM analytics.orders o "
            "JOIN analytics.customers c ON o.customer_id = c.id"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=POLICY,
    )

    assert result.join_count == 1


@pytest.mark.parametrize(
    ("sql", "expected_limit"),
    [
        ("SELECT analytics.orders.id FROM analytics.orders", "LIMIT 10"),
        ("SELECT analytics.orders.id FROM analytics.orders LIMIT 4", "LIMIT 4"),
        ("SELECT analytics.orders.id FROM analytics.orders LIMIT 99", "LIMIT 10"),
    ],
)
def test_output_limit_is_enforced(sql: str, expected_limit: str) -> None:
    result = SQLValidator().validate(sql=sql, dialect=SQLDialect.POSTGRES, policy=POLICY)
    assert expected_limit in result.normalized_sql


def test_parameterized_limit_is_rejected() -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(
            sql="SELECT analytics.orders.id FROM analytics.orders LIMIT :limit",
            dialect=SQLDialect.POSTGRES,
            policy=POLICY,
        )
