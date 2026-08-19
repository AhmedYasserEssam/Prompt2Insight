from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from itertools import pairwise
from typing import Any, Literal

from app.application.analytics.chart_recommendation import column_kind
from app.domain.analytics.models import ResultTable

SummaryShape = Literal[
    "scalar",
    "single_row_multi_metric",
    "ranking",
    "categorical_comparison",
    "time_series",
    "generic_table",
    "generic_fallback",
]

_MAX_LISTED_ROWS = 20
_COMPARISON_DIMENSION_TOKENS = {
    "category",
    "channel",
    "country",
    "region",
    "segment",
    "status",
    "type",
}
_ARABIC_COLUMN_LABELS = {
    "average_order_total": "متوسط إجمالي الطلب",
    "category": "الفئة",
    "city": "المدينة",
    "customer": "العميل",
    "month": "الشهر",
    "order_count": "عدد الطلبات",
    "order_month": "شهر الطلب",
    "product_name": "اسم المنتج",
    "region": "المنطقة",
    "revenue": "الإيرادات",
    "segment": "الشريحة",
    "total_revenue": "إجمالي الإيرادات",
    "total_sales": "إجمالي المبيعات",
}


def deterministic_answer(table: ResultTable, language: str) -> str:
    """Summarize only values and structure present in an executed result table."""
    if not table.rows:
        return (
            "لم يتم إرجاع صفوف مطابقة."
            if language == "ar"
            else "No matching rows were returned."
        )

    shape = _classify(table)
    if shape == "scalar":
        return _scalar_summary(table, language)
    if shape == "single_row_multi_metric":
        return _multi_metric_summary(table, language)
    if shape == "time_series":
        return _time_series_summary(table, language)
    if shape == "ranking":
        return _category_summary(table, language, shape)
    if shape == "categorical_comparison":
        return _category_summary(table, language, shape)
    if shape == "generic_table":
        return _table_summary(table, language)
    return _generic_fallback(len(table.rows), language)


def _classify(table: ResultTable) -> SummaryShape:
    if not table.columns:
        return "generic_fallback"
    if (
        len(table.rows) == 1
        and len(table.columns) == 1
        and _complete_rows(table, 0)
    ):
        return "scalar"

    kinds = [column_kind(table, column) for column in table.columns]
    numeric_indexes = [index for index, kind in enumerate(kinds) if kind == "numeric"]
    temporal_indexes = [index for index, kind in enumerate(kinds) if kind == "temporal"]
    categorical_indexes = [
        index for index, kind in enumerate(kinds) if kind == "categorical"
    ]

    if (
        len(table.rows) == 1
        and len(table.columns) > 1
        and _complete_rows(table, *range(len(table.columns)))
    ):
        return "single_row_multi_metric"

    if temporal_indexes and numeric_indexes:
        period_index = temporal_indexes[0]
        value_index = numeric_indexes[0]
        if _complete_rows(table, period_index, value_index) and _numeric_values(
            table, value_index
        ) is not None:
            return "time_series"

    if (
        len(table.columns) == 2
        and len(categorical_indexes) == 1
        and len(numeric_indexes) == 1
        and 2 <= len(table.rows) <= _MAX_LISTED_ROWS
        and _complete_rows(table, categorical_indexes[0], numeric_indexes[0])
    ):
        values = _numeric_values(table, numeric_indexes[0])
        if values is not None:
            ordered = all(left >= right for left, right in pairwise(values)) or all(
                left <= right for left, right in pairwise(values)
            )
            category_column = table.columns[categorical_indexes[0]]
            if (
                ordered
                and len(set(values)) > 1
                and not _is_comparison_dimension(category_column)
            ):
                return "ranking"
            return "categorical_comparison"

    return "generic_table"


def _scalar_summary(table: ResultTable, language: str) -> str:
    label = _label(table.columns[0], language)
    value = _format_value(table.rows[0][0])
    if language == "ar":
        return f"قيمة {label} هي {value}."
    return f"The {label} is {value}."


def _multi_metric_summary(table: ResultTable, language: str) -> str:
    separator = "؛ " if language == "ar" else "; "
    values = separator.join(
        f"{_label(column, language)}: {_format_value(table.rows[0][index])}"
        for index, column in enumerate(table.columns)
    )
    return f"النتيجة هي {values}." if language == "ar" else f"The result is {values}."


def _time_series_summary(table: ResultTable, language: str) -> str:
    kinds = [column_kind(table, column) for column in table.columns]
    period_index = kinds.index("temporal")
    value_index = kinds.index("numeric")
    values = _numeric_values(table, value_index)
    assert values is not None

    indexed_rows = list(zip(table.rows, values, strict=True))
    first_row, _ = min(
        indexed_rows, key=lambda item: _temporal_key(item[0][period_index])
    )
    last_row, _ = max(
        indexed_rows, key=lambda item: _temporal_key(item[0][period_index])
    )
    highest_row, _ = max(indexed_rows, key=lambda item: item[1])
    lowest_row, _ = min(indexed_rows, key=lambda item: item[1])

    highest_period = _format_period(highest_row[period_index], language)
    lowest_period = _format_period(lowest_row[period_index], language)
    highest_value = _format_number(highest_row[value_index])
    lowest_value = _format_number(lowest_row[value_index])
    metric = _label(table.columns[value_index], language)

    if language == "ar":
        return (
            f"بلغت {metric} ذروتها عند {highest_value} في {highest_period}، "
            f"ووصلت إلى أدنى مستوى عند {lowest_value} في {lowest_period}."
        )
    return (
        f"{metric.capitalize()} peaked at {highest_value} in {highest_period} and reached "
        f"its lowest point of {lowest_value} in {lowest_period}."
    )


def _category_summary(
    table: ResultTable,
    language: str,
    shape: Literal["ranking", "categorical_comparison"],
) -> str:
    kinds = [column_kind(table, column) for column in table.columns]
    category_index = kinds.index("categorical")
    value_index = kinds.index("numeric")
    separator = "؛ " if language == "ar" else "; "
    items = separator.join(
        f"{_format_value(row[category_index])} — {_format_value(row[value_index])}"
        for row in table.rows
    )
    metric = _label(table.columns[value_index], language)

    if language == "ar":
        noun = "الترتيب" if shape == "ranking" else "المقارنة"
        return f"{noun} حسب {metric} هو: {items}."
    if shape == "ranking":
        return f"The returned {metric} ranking is: {items}."
    return f"The {metric} comparison is: {items}."


def _table_summary(table: ResultTable, language: str) -> str:
    row_count = len(table.rows)
    columns = _joined_labels(table.columns, language)
    if language == "ar":
        return f"تحتوي النتيجة على {row_count} صفوف بالأعمدة: {columns}."
    noun = "row" if row_count == 1 else "rows"
    return f"The result contains {row_count} {noun} with {columns}."


def _generic_fallback(row_count: int, language: str) -> str:
    if language == "ar":
        return f"تم تنفيذ الاستعلام بنجاح. تم إرجاع {row_count} صفوف."
    noun = "row" if row_count == 1 else "rows"
    return f"Query completed successfully. {row_count} {noun} returned."


def _complete_rows(table: ResultTable, *indexes: int) -> bool:
    return all(
        all(index < len(row) and row[index] is not None for index in indexes)
        for row in table.rows
    )


def _numeric_values(table: ResultTable, index: int) -> list[Decimal] | None:
    values: list[Decimal] = []
    for row in table.rows:
        try:
            value = Decimal(str(row[index]))
        except (IndexError, InvalidOperation, ValueError):
            return None
        if not value.is_finite():
            return None
        values.append(value)
    return values


def _temporal_key(value: Any) -> str:
    return _format_value(value)


def _format_value(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _format_number(value: Any) -> str:
    try:
        number = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return _format_value(value)
    return format(number, ",f").rstrip("0").rstrip(".")


def _format_period(value: Any, language: str) -> str:
    raw = _format_value(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{raw}-01")
        except ValueError:
            return raw
    if language == "ar":
        months = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
        return f"{months[parsed.month - 1]} {parsed.year}"
    return parsed.strftime("%B %Y")


def _label(column: str, language: str) -> str:
    if language == "ar" and column.casefold() in _ARABIC_COLUMN_LABELS:
        return _ARABIC_COLUMN_LABELS[column.casefold()]
    return " ".join(column.replace("_", " ").split()) or column


def _joined_labels(columns: list[str], language: str) -> str:
    labels = [_label(column, language) for column in columns]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        conjunction = " و" if language == "ar" else " and "
        return conjunction.join(labels)
    if language == "ar":
        return "، ".join(labels[:-1]) + "، و" + labels[-1]
    return ", ".join(labels[:-1]) + ", and " + labels[-1]


def _is_comparison_dimension(column: str) -> bool:
    tokens = set(column.casefold().replace("_", " ").split())
    return bool(tokens & _COMPARISON_DIMENSION_TOKENS)
