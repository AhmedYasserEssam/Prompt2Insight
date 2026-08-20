import json
from datetime import date
from decimal import Decimal

from app.application.analytics.chart_recommendation import (
    ChartPolicy,
    recommend_chart,
)
from app.domain.analytics.models import ChartSpecification, ResultTable


def test_chart_contract_contains_only_semantic_fields() -> None:
    properties = ChartSpecification.model_json_schema()["properties"]

    assert set(properties) == {
        "type",
        "x_column",
        "y_columns",
        "series_column",
        "title",
        "x_label",
        "y_label",
    }
    assert not {
        "colors",
        "margins",
        "bar_width",
        "fill_opacity",
        "tick_density",
        "number_format",
        "date_format",
    } & properties.keys()


def test_result_table_serializes_decimals_as_numbers_without_mutating_raw_values() -> None:
    value = Decimal("827455.873")
    table = ResultTable(columns=["total_sales"], rows=[[value]])

    payload = json.loads(table.model_dump_json())

    assert payload["rows"] == [[827455.873]]
    assert table.rows == [[value]]
    assert isinstance(table.rows[0][0], Decimal)


def test_category_and_numeric_recommends_bar() -> None:
    table = ResultTable(
        columns=["region", "total_sales"],
        rows=[["East", 10], ["West", 20], ["North", 30]],
    )

    result = recommend_chart(table)

    assert result.chart is not None
    assert result.chart.chart_type == "bar"
    assert result.chart.x_column == "region"
    assert result.chart.y_columns == ["total_sales"]


def test_long_category_labels_recommend_horizontal_bar() -> None:
    table = ResultTable(
        columns=["category", "total_sales"],
        rows=[
            ["Technology", 827455.873],
            ["Furniture", 728658.5757],
            ["Office Supplies", 705422.334],
        ],
    )

    result = recommend_chart(table)

    assert result.chart is not None
    assert result.chart.chart_type == "horizontal_bar"


def test_temporal_and_numeric_recommends_line() -> None:
    table = ResultTable(
        columns=["month", "revenue"],
        rows=[[date(2025, 2, 1), 20], [date(2025, 1, 1), 10]],
    )

    result = recommend_chart(table)

    assert result.chart is not None
    assert result.chart.chart_type == "line"
    assert result.chart.x_column == "month"


def test_temporal_long_form_recommends_multi_line() -> None:
    table = ResultTable(
        columns=["month", "category", "total_sales"],
        rows=[
            ["2025-01-01", "Furniture", 10],
            ["2025-01-01", "Technology", 20],
            ["2025-02-01", "Furniture", 15],
            ["2025-02-01", "Technology", 25],
        ],
    )

    result = recommend_chart(table)

    assert result.chart is not None
    assert result.chart.chart_type == "line"
    assert result.chart.series_column == "category"
    assert result.chart.y_columns == ["total_sales"]


def test_two_meaningful_numeric_columns_recommend_scatter() -> None:
    table = ResultTable(
        columns=["discount", "profit"],
        rows=[[1, 3], [2, 5], [3, 8]],
    )

    result = recommend_chart(table)

    assert result.chart is not None
    assert result.chart.chart_type == "scatter"
    assert result.chart.x_column == "discount"
    assert result.chart.y_columns == ["profit"]


def test_single_metric_recommends_kpi() -> None:
    table = ResultTable(columns=["average_order_total"], rows=[[243.81]])

    result = recommend_chart(table)

    assert result.chart is not None
    assert result.chart.chart_type == "kpi"
    assert result.chart.x_column is None
    assert result.chart.y_columns == ["average_order_total"]


def test_too_many_categories_omits_chart_without_removing_rows() -> None:
    rows = [[f"Category {index}", index] for index in range(21)]
    table = ResultTable(columns=["category", "sales"], rows=rows)

    result = recommend_chart(table, policy=ChartPolicy(max_bar_categories=20))

    assert result.chart is None
    assert table.rows == rows


def test_category_line_and_temporal_area_are_normalized() -> None:
    categorical = ResultTable(
        columns=["category", "sales"], rows=[["A", 1], ["B", 2]]
    )
    temporal = ResultTable(
        columns=["month", "sales"], rows=[["2025-01-01", 1], ["2025-02-01", 2]]
    )

    category_result = recommend_chart(
        categorical,
        ChartSpecification(type="line", x_column="category", y_columns=["sales"]),
    )
    temporal_result = recommend_chart(
        temporal,
        ChartSpecification(type="area", x_column="month", y_columns=["sales"]),
    )

    assert category_result.chart is not None
    assert category_result.chart.chart_type == "bar"
    assert temporal_result.chart is not None
    assert temporal_result.chart.chart_type == "line"


def test_donut_requires_small_nonnegative_part_to_whole_result() -> None:
    table = ResultTable(
        columns=["category", "share"], rows=[["A", 60], ["B", 40]]
    )
    suggestion = ChartSpecification(
        type="donut", x_column="category", y_columns=["share"]
    )

    result = recommend_chart(table, suggestion)

    assert result.chart is not None
    assert result.chart.chart_type == "donut"


def test_missing_or_non_numeric_suggested_columns_are_nonfatal_omissions() -> None:
    table = ResultTable(
        columns=["category", "sales"], rows=[["A", 1], ["B", 2]]
    )

    missing_x = recommend_chart(
        table,
        ChartSpecification(type="bar", x_column="missing", y_columns=["sales"]),
    )
    missing_y = recommend_chart(
        table,
        ChartSpecification(type="bar", x_column="category", y_columns=["missing"]),
    )
    non_numeric_y = recommend_chart(
        table,
        ChartSpecification(type="bar", x_column="sales", y_columns=["category"]),
    )

    assert missing_x.chart is None and missing_x.suggestion_rejected
    assert missing_y.chart is None and missing_y.suggestion_rejected
    assert non_numeric_y.chart is None and non_numeric_y.suggestion_rejected


def test_wide_category_result_keeps_multiple_numeric_series() -> None:
    table = ResultTable(
        columns=["category", "sales", "profit"],
        rows=[["A", 10, 2], ["B", 12, 3]],
    )

    result = recommend_chart(table)

    assert result.chart is not None
    assert result.chart.chart_type == "bar"
    assert result.chart.y_columns == ["sales", "profit"]
