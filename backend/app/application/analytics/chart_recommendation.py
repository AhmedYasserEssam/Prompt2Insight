import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from app.domain.analytics.models import ChartSpecification, ResultTable

ColumnKind = Literal["numeric", "temporal", "categorical", "empty"]

_TEMPORAL_NAME = re.compile(
    r"(?:^|_)(?:date|datetime|day|week|month|quarter|year|time|timestamp)(?:_|$)",
    re.IGNORECASE,
)
_IDENTIFIER_NAME = re.compile(r"(?:^id$|_id$|^uuid$|_uuid$)", re.IGNORECASE)
_ISO_TEMPORAL = re.compile(
    r"^\d{4}(?:-\d{2}(?:-\d{2})?)?(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


@dataclass(frozen=True, slots=True)
class ChartPolicy:
    max_bar_categories: int = 20
    max_donut_categories: int = 6
    max_series: int = 8


@dataclass(frozen=True, slots=True)
class ChartRecommendation:
    chart: ChartSpecification | None
    suggestion_rejected: bool = False


@dataclass(frozen=True, slots=True)
class _ColumnProfile:
    name: str
    index: int
    kind: ColumnKind
    cardinality: int
    values: tuple[Any, ...]


def recommend_chart(
    table: ResultTable,
    suggestion: ChartSpecification | None = None,
    policy: ChartPolicy | None = None,
) -> ChartRecommendation:
    """Return one data-compatible semantic visualization without changing result rows."""
    policy = policy or ChartPolicy()
    if not table.rows or not table.columns:
        return ChartRecommendation(None, suggestion_rejected=suggestion is not None)

    profiles = _profiles(table)
    by_name = {profile.name: profile for profile in profiles}
    if suggestion is not None and not _references_exist(suggestion, by_name):
        return ChartRecommendation(None, suggestion_rejected=True)

    numeric = [profile for profile in profiles if profile.kind == "numeric"]
    meaningful_numeric = [
        profile for profile in numeric if not _IDENTIFIER_NAME.search(profile.name)
    ]
    if len(table.rows) == 1 and meaningful_numeric:
        selected = _suggested_numeric(suggestion, by_name) or meaningful_numeric
        return ChartRecommendation(
            _chart(
                "kpi",
                None,
                [profile.name for profile in selected],
                suggestion=suggestion,
            )
        )

    if suggestion is not None:
        normalized = _normalize_suggestion(suggestion, by_name, policy)
        if normalized is not None:
            return ChartRecommendation(normalized)
        return ChartRecommendation(None, suggestion_rejected=True)

    temporal = [profile for profile in profiles if profile.kind == "temporal"]
    categorical = [profile for profile in profiles if profile.kind == "categorical"]

    if temporal and meaningful_numeric:
        x = temporal[0]
        series = _series_candidate(categorical, policy)
        y = meaningful_numeric if series is None else meaningful_numeric[:1]
        return ChartRecommendation(
            _chart("line", x.name, [item.name for item in y], series=series)
        )

    if categorical and meaningful_numeric:
        x = categorical[0]
        if x.cardinality > policy.max_bar_categories:
            return ChartRecommendation(None)
        other_categories = [item for item in categorical[1:] if item.name != x.name]
        series = _series_candidate(other_categories, policy)
        y = meaningful_numeric if series is None else meaningful_numeric[:1]
        chart_type = _bar_type(x)
        return ChartRecommendation(
            _chart(chart_type, x.name, [item.name for item in y], series=series)
        )

    if len(meaningful_numeric) == 2 and _meaningful_scatter(meaningful_numeric):
        return ChartRecommendation(
            _chart(
                "scatter",
                meaningful_numeric[0].name,
                [meaningful_numeric[1].name],
            )
        )
    return ChartRecommendation(None)


def column_kind(table: ResultTable, column: str) -> ColumnKind | None:
    return next(
        (profile.kind for profile in _profiles(table) if profile.name == column),
        None,
    )


def _normalize_suggestion(
    suggestion: ChartSpecification,
    profiles: dict[str, _ColumnProfile],
    policy: ChartPolicy,
) -> ChartSpecification | None:
    y_profiles = [profiles[column] for column in suggestion.y_columns]
    if any(profile.kind != "numeric" for profile in y_profiles):
        return None
    if suggestion.series_column is not None:
        series = profiles[suggestion.series_column]
        if series.kind != "categorical" or series.cardinality > policy.max_series:
            return None

    if suggestion.chart_type == "kpi":
        return None
    if suggestion.x_column is None:
        return None
    x = profiles[suggestion.x_column]
    if x.kind == "empty":
        return None

    if x.kind == "temporal":
        return _chart("line", x.name, suggestion.y_columns, suggestion=suggestion)

    if suggestion.chart_type == "scatter":
        if x.kind == "numeric" and len(y_profiles) == 1 and _meaningful_scatter([x, *y_profiles]):
            return _chart("scatter", x.name, suggestion.y_columns, suggestion=suggestion)
        return None

    if x.kind != "categorical":
        return None
    if x.cardinality > policy.max_bar_categories:
        return None
    if suggestion.chart_type == "donut":
        if (
            len(y_profiles) == 1
            and suggestion.series_column is None
            and 2 <= x.cardinality <= policy.max_donut_categories
            and len(x.values) == x.cardinality
            and _is_part_to_whole(y_profiles[0])
        ):
            return _chart("donut", x.name, suggestion.y_columns, suggestion=suggestion)
        return None
    return _chart(_bar_type(x), x.name, suggestion.y_columns, suggestion=suggestion)


def _profiles(table: ResultTable) -> list[_ColumnProfile]:
    profiles: list[_ColumnProfile] = []
    for index, name in enumerate(table.columns):
        values = tuple(
            row[index]
            for row in table.rows
            if index < len(row) and row[index] is not None
        )
        profiles.append(
            _ColumnProfile(
                name=name,
                index=index,
                kind=_infer_kind(name, values),
                cardinality=len({_hashable(value) for value in values}),
                values=values,
            )
        )
    return profiles


def _infer_kind(name: str, values: tuple[Any, ...]) -> ColumnKind:
    if not values:
        return "empty"
    if all(_is_temporal(value) for value in values):
        return "temporal"
    if _TEMPORAL_NAME.search(name) and all(_is_temporal_like(value) for value in values):
        return "temporal"
    if all(_is_number(value) for value in values):
        return "numeric"
    return "categorical"


def _is_temporal(value: Any) -> bool:
    return isinstance(value, (date, datetime)) and not isinstance(value, bool)


def _is_temporal_like(value: Any) -> bool:
    if _is_temporal(value):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return 1000 <= value <= 9999
    return isinstance(value, str) and bool(_ISO_TEMPORAL.fullmatch(value.strip()))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _references_exist(
    suggestion: ChartSpecification, profiles: dict[str, _ColumnProfile]
) -> bool:
    references = [*suggestion.y_columns]
    if suggestion.x_column is not None:
        references.append(suggestion.x_column)
    if suggestion.series_column is not None:
        references.append(suggestion.series_column)
    return all(reference in profiles for reference in references)


def _suggested_numeric(
    suggestion: ChartSpecification | None, profiles: dict[str, _ColumnProfile]
) -> list[_ColumnProfile]:
    if suggestion is None:
        return []
    selected = [profiles[column] for column in suggestion.y_columns]
    return selected if all(profile.kind == "numeric" for profile in selected) else []


def _series_candidate(
    profiles: list[_ColumnProfile], policy: ChartPolicy
) -> str | None:
    if not profiles:
        return None
    candidate = profiles[0]
    if 2 <= candidate.cardinality <= policy.max_series:
        return candidate.name
    return None


def _bar_type(profile: _ColumnProfile) -> Literal["bar", "horizontal_bar"]:
    labels = [str(value) for value in profile.values]
    return (
        "horizontal_bar"
        if profile.cardinality > 8 or max((len(label) for label in labels), default=0) >= 14
        else "bar"
    )


def _meaningful_scatter(profiles: list[_ColumnProfile]) -> bool:
    return (
        len(profiles) == 2
        and all(not _IDENTIFIER_NAME.search(profile.name) for profile in profiles)
        and all(profile.cardinality >= 3 for profile in profiles)
    )


def _is_part_to_whole(profile: _ColumnProfile) -> bool:
    values = [float(value) for value in profile.values]
    return bool(values) and all(value >= 0 for value in values) and sum(values) > 0


def _chart(
    chart_type: Literal[
        "bar", "horizontal_bar", "line", "area", "scatter", "donut", "kpi"
    ],
    x_column: str | None,
    y_columns: list[str],
    *,
    series: str | None = None,
    suggestion: ChartSpecification | None = None,
) -> ChartSpecification:
    return ChartSpecification(
        chart_type=chart_type,
        x_column=x_column,
        y_columns=y_columns,
        series_column=(suggestion.series_column if suggestion is not None else series),
        title=(suggestion.title if suggestion is not None else _default_title(y_columns)),
        x_label=(suggestion.x_label if suggestion is not None else None),
        y_label=(suggestion.y_label if suggestion is not None else None),
    )


def _default_title(columns: list[str]) -> str:
    return " · ".join(column.replace("_", " ").strip().title() for column in columns)


def _hashable(value: Any) -> Any:
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value
