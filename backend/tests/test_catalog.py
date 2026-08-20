from pathlib import Path

import pytest

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect
from app.infrastructure.catalogs.loader import load_catalog

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


def test_catalog_hash_identifies_the_loaded_revision() -> None:
    _, revision_hash = load_catalog(CATALOG_PATH)
    assert len(revision_hash) == 64
    assert revision_hash == load_catalog(CATALOG_PATH)[1]


def test_legacy_policy_metadata_is_accepted_but_not_part_of_current_catalog() -> None:
    from app.infrastructure.catalogs.models import AnalyticsCatalog

    catalog = AnalyticsCatalog.model_validate(
        {
            "catalog_version": "legacy",
            "metrics": {},
            "dimensions": {},
            "join_contracts": [{"left": "x.a", "right": "y.a"}],
            "column_policies": {"x.secret": "sensitive"},
            "privacy": {"privacy_unit": "x.id", "minimum_group_size": 5},
        }
    )

    assert catalog.model_dump() == {
        "catalog_version": "legacy",
        "metrics": {},
        "dimensions": {},
    }
