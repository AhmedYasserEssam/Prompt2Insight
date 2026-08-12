import pytest

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect
from app.infrastructure.sql.validator import SQLPolicy, SQLValidator

POLICY = SQLPolicy(
    allowed_tables=frozenset({"analytics.orders", "analytics.order_items", "analytics.customers"}),
    allowed_columns=frozenset(
        {
            "analytics.orders.id",
            "analytics.orders.region",
            "analytics.orders.customer_id",
            "analytics.order_items.order_id",
            "analytics.order_items.net_amount",
            "analytics.customers.email",
            "analytics.customers.phone",
        }
    ),
    sensitive_columns=frozenset({"analytics.customers.email"}),
    prohibited_columns=frozenset({"analytics.customers.phone"}),
    allowed_joins=frozenset(
        {
            ("analytics.orders.id", "analytics.order_items.order_id", "inner"),
            ("analytics.orders.id", "analytics.order_items.order_id", "left"),
        }
    ),
    maximum_rows=10,
)


@pytest.mark.parametrize("dialect", [SQLDialect.POSTGRES, SQLDialect.MYSQL])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM analytics.orders",
        "WITH x AS (SELECT id FROM analytics.orders) SELECT id FROM x",
    ],
)
def test_safe_selects_are_allowed(dialect: SQLDialect, sql: str) -> None:
    assert SQLValidator().validate(sql=sql, dialect=dialect, policy=POLICY).normalized_sql


@pytest.mark.parametrize("dialect", [SQLDialect.POSTGRES, SQLDialect.MYSQL])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM analytics.orders; SELECT id FROM analytics.orders",
        "SELECT ';' FROM analytics.orders; SELECT id FROM analytics.orders",
        "SELECT id FROM analytics.orders /* ; */; SELECT id FROM analytics.orders",
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
def test_mutation_and_multiple_statements_are_rejected(dialect: SQLDialect, sql: str) -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(sql=sql, dialect=dialect, policy=POLICY)


@pytest.mark.parametrize(
    "dialect, sql",
    [
        (SQLDialect.POSTGRES, "SELECT id INTO new_table FROM analytics.orders"),
        (SQLDialect.POSTGRES, "SELECT id FROM analytics.orders FOR UPDATE"),
        (SQLDialect.POSTGRES, "SELECT id FROM analytics.orders FOR SHARE"),
        (SQLDialect.MYSQL, "SELECT id FROM analytics.orders INTO OUTFILE '/tmp/x'"),
        (SQLDialect.MYSQL, "SELECT id FROM analytics.orders INTO DUMPFILE '/tmp/x'"),
    ],
)
def test_dangerous_select_forms_are_rejected(dialect: SQLDialect, sql: str) -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(sql=sql, dialect=dialect, policy=POLICY)


@pytest.mark.parametrize("dialect", [SQLDialect.POSTGRES, SQLDialect.MYSQL])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM pg_catalog.pg_tables",
        "SELECT table_name FROM information_schema.tables",
        "SELECT id FROM mysql.user",
        "SELECT id FROM performance_schema.events_statements_current",
        "SELECT id FROM sys.sys_config",
        "SELECT id FROM analytics.unknown",
        "SELECT unknown FROM analytics.orders",
        "SELECT email FROM analytics.customers",
        "SELECT phone FROM analytics.customers",
        "SELECT SUM(email) FROM analytics.customers",
        "SELECT customers.* FROM analytics.customers",
        "SELECT * FROM analytics.orders",
        "SELECT pg_sleep(1) FROM analytics.orders",
        "SELECT sleep(1) FROM analytics.orders",
        "SELECT arbitrary_udf(id) FROM analytics.orders",
    ],
)
def test_table_column_and_function_policy_is_fail_closed(dialect: SQLDialect, sql: str) -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(sql=sql, dialect=dialect, policy=POLICY)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT o.id FROM analytics.orders o JOIN analytics.order_items i ON o.id = i.order_id",
        "SELECT COUNT(o.id) FROM analytics.orders o",
        "SELECT DATE_TRUNC('month', o.id) FROM analytics.orders o",
    ],
)
def test_approved_joins_and_functions(sql: str) -> None:
    SQLValidator().validate(sql=sql, dialect=SQLDialect.POSTGRES, policy=POLICY)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT o.id FROM analytics.orders o CROSS JOIN analytics.order_items i",
        "SELECT o.id FROM analytics.orders o NATURAL JOIN analytics.order_items i",
        (
            "SELECT o.id FROM analytics.orders o RIGHT JOIN analytics.order_items i "
            "ON o.id = i.order_id"
        ),
        "SELECT o.id FROM analytics.orders o JOIN analytics.order_items i ON o.id = i.net_amount",
        "SELECT a.id FROM analytics.orders a JOIN analytics.orders b ON a.id = b.id",
    ],
)
def test_unapproved_join_forms_are_rejected(sql: str) -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(sql=sql, dialect=SQLDialect.POSTGRES, policy=POLICY)


def test_error_categories_are_specific() -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        SQLValidator().validate(
            sql="SELECT id FROM pg_catalog.pg_tables", dialect=SQLDialect.POSTGRES, policy=POLICY
        )
    assert captured.value.code is ErrorCode.UNAUTHORIZED_TABLE


@pytest.mark.parametrize(
    ("sql", "expected_limit"),
    [
        ("SELECT id FROM analytics.orders", "LIMIT 10"),
        ("SELECT id FROM analytics.orders LIMIT 4", "LIMIT 4"),
        ("SELECT id FROM analytics.orders LIMIT 10", "LIMIT 10"),
        ("SELECT id FROM analytics.orders LIMIT 99", "LIMIT 10"),
    ],
)
def test_output_limit_is_enforced(sql: str, expected_limit: str) -> None:
    result = SQLValidator().validate(sql=sql, dialect=SQLDialect.POSTGRES, policy=POLICY)
    assert expected_limit in result.normalized_sql


def test_parameterized_limit_is_rejected() -> None:
    with pytest.raises(Prompt2InsightError):
        SQLValidator().validate(
            sql="SELECT id FROM analytics.orders LIMIT :limit",
            dialect=SQLDialect.POSTGRES,
            policy=POLICY,
        )
