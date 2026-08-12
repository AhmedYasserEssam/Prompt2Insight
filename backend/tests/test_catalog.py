from pathlib import Path

import pytest

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect
from app.infrastructure.catalogs.loader import load_catalog
from app.infrastructure.sql.validator import SQLPolicy, SQLValidator

CATALOG_PATH = Path(__file__).parents[2] / "catalogs" / "analytics_catalog.example.yaml"


@pytest.fixture
def catalog():
    return load_catalog(CATALOG_PATH)[0]


def test_english_and_arabic_metric_aliases_resolve_to_the_same_id(catalog) -> None:
    assert catalog.resolve_metric_id("total revenue") == "revenue"
    assert catalog.resolve_metric_id("إجمالي الإيرادات") == "revenue"
    assert catalog.resolve_metric_id("ايرادات") == "revenue"
    assert catalog.resolve_dimension_id("monthly") == "order_month"
    assert catalog.resolve_dimension_id("حسب الشهر") == "order_month"


def test_catalog_returns_only_declared_metric_expressions(catalog) -> None:
    assert catalog.metric_expression("revenue", SQLDialect.POSTGRES) == (
        "SUM(analytics.order_items.net_amount)"
    )
    with pytest.raises(Prompt2InsightError) as captured:
        catalog.metric_expression("profit", SQLDialect.POSTGRES)
    assert captured.value.code is ErrorCode.METRIC_UNDEFINED


def test_catalog_rejects_invalid_metric_dimension_combinations(catalog) -> None:
    with pytest.raises(Prompt2InsightError):
        catalog.validate_metric_dimensions(["revenue"], ["unknown_dimension"])


def test_catalog_hash_identifies_the_loaded_revision() -> None:
    _, revision_hash = load_catalog(CATALOG_PATH)
    assert len(revision_hash) == 64
    assert revision_hash == load_catalog(CATALOG_PATH)[1]


def test_catalog_policy_rejects_undeclared_joins(catalog) -> None:
    policy = SQLPolicy.from_catalog(
        catalog=catalog,
        allowed_tables=frozenset(
            {"analytics.order_items", "analytics.orders", "analytics.products"}
        ),
    )
    validator = SQLValidator()

    validator.validate(
        sql=(
            "SELECT oi.order_id FROM analytics.order_items oi "
            "JOIN analytics.orders o ON oi.order_id = o.id"
        ),
        dialect=SQLDialect.POSTGRES,
        policy=policy,
    )

    with pytest.raises(Prompt2InsightError) as captured:
        validator.validate(
            sql=(
                "SELECT oi.order_id FROM analytics.order_items oi "
                "JOIN analytics.products p ON oi.order_id = p.id"
            ),
            dialect=SQLDialect.POSTGRES,
            policy=policy,
        )
    assert captured.value.code is ErrorCode.SQL_POLICY_REJECTED


def test_privacy_rule_suppresses_small_groups(catalog) -> None:
    assert catalog.privacy.suppresses(4)
    assert not catalog.privacy.suppresses(5)
