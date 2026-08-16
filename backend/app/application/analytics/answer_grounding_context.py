import re
from datetime import date, timedelta

from app.domain.analytics.models import (
    AnswerGroundingContext,
    DateRangeGrounding,
    NumericFilterGrounding,
    QueryParameter,
    QueryPlan,
)

_PARAMETER = re.compile(r"(?<!:):(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
_LIMIT = re.compile(r"\bLIMIT\s+(?P<value>[0-9]+)\b", re.IGNORECASE)
_COMPARISON = re.compile(
    r"(?P<field>[A-Za-z_][A-Za-z0-9_.$\"]*)\s*"
    r"(?P<operator>>=|<=|<>|!=|=|>|<)\s*:(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def build_answer_grounding_context(
    *, plan: QueryPlan, executed_sql: str
) -> AnswerGroundingContext:
    """Expose only parameter values and limits that survived validation and execution."""
    parameter_names = {match.group("name") for match in _PARAMETER.finditer(executed_sql)}
    parameters = {
        parameter.name: parameter
        for parameter in plan.parameters
        if parameter.name in parameter_names
    }
    comparisons = {
        match.group("name"): match
        for match in _COMPARISON.finditer(executed_sql)
        if match.group("name") in parameters
    }
    return AnswerGroundingContext(
        date_ranges=_date_ranges(parameters, comparisons, executed_sql),
        numeric_filters=_numeric_filters(parameters, comparisons),
        top_n=_explicit_top_n(plan.sql, executed_sql),
    )


def _date_ranges(
    parameters: dict[str, QueryParameter],
    comparisons: dict[str, re.Match[str]],
    executed_sql: str,
) -> list[DateRangeGrounding]:
    output: list[DateRangeGrounding] = []
    for name, start_parameter in parameters.items():
        if start_parameter.type != "date" or not name.startswith("start_"):
            continue
        end_name = f"end_{name.removeprefix('start_')}"
        end_parameter = parameters.get(end_name)
        if end_parameter is None or end_parameter.type != "date":
            continue
        start = start_parameter.binding_value()
        end = end_parameter.binding_value()
        if not isinstance(start, date) or not isinstance(end, date):
            continue
        end_comparison = comparisons.get(end_name)
        if end_comparison is not None and end_comparison.group("operator") == "<":
            end -= timedelta(days=1)
        output.append(DateRangeGrounding(start=start.isoformat(), end=end.isoformat()))
    return output


def _numeric_filters(
    parameters: dict[str, QueryParameter], comparisons: dict[str, re.Match[str]]
) -> list[NumericFilterGrounding]:
    operators = {"=": "eq", "!=": "ne", "<>": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
    output: list[NumericFilterGrounding] = []
    for name, comparison in comparisons.items():
        parameter = parameters[name]
        if parameter.type not in {"integer", "number"}:
            continue
        value = parameter.binding_value()
        if type(value) not in {int, float}:
            continue
        output.append(
            NumericFilterGrounding(
                field=comparison.group("field").rsplit(".", maxsplit=1)[-1].strip('"'),
                operator=operators[comparison.group("operator")],
                value=value,
            )
        )
    return output


def _explicit_top_n(planned_sql: str | None, executed_sql: str) -> int | None:
    if planned_sql is None:
        return None
    planned_limit = _LIMIT.search(planned_sql)
    executed_limit = _LIMIT.search(executed_sql)
    if planned_limit is None or executed_limit is None:
        return None
    if planned_limit.group("value") != executed_limit.group("value"):
        return None
    return int(executed_limit.group("value"))
