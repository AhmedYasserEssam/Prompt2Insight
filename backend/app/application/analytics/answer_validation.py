import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerOutput, ResultTable

_NUMBER = re.compile(
    r"(?<![\w.])-?[0-9\u0660-\u0669][0-9\u0660-\u0669,]*"
    r"(?:[.\u066b][0-9\u0660-\u0669]+)?(?:[eE][+-]?[0-9]+)?[%\u066a]?(?!\w)"
)
_ARABIC_DIGITS = {**{0x0660 + index: str(index) for index in range(10)}, 0x066B: "."}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ].+$")


@dataclass(frozen=True, slots=True)
class _NumericToken:
    value: Decimal
    is_percentage: bool = False


def validate_answer_output(output: AnswerOutput, table: ResultTable) -> None:
    """Ensure an answer can only refer to columns and numeric values actually returned."""
    if output.chart is not None:
        references = [output.chart.x_column, *output.chart.y_columns]
        if any(column not in table.columns for column in references):
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                "The answer chart references columns outside the executed result schema.",
            )

    result_tokens: set[_NumericToken] = set()
    for row in table.rows:
        for value in row:
            if (number := _as_numeric_token(value)) is not None:
                result_tokens.add(number)
            if (year := _grounded_date_year(value)) is not None:
                result_tokens.add(_NumericToken(year))
    text = "\n".join(
        [
            output.answer,
            *output.insights,
            *output.warnings,
            *([output.chart.title] if output.chart is not None else []),
        ]
    )
    for match in _NUMBER.findall(text):
        token = _as_numeric_token(match)
        if token is None or token not in result_tokens:
            normalized = token.value if token is not None else None
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                f"The answer contains an ungrounded numeric value: {match!r} "
                f"(normalized={normalized!s}).",
            )


def _as_numeric_token(value: Any) -> _NumericToken | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return _NumericToken(Decimal(str(value)))
    if not isinstance(value, str):
        return None
    normalized = value.translate(_ARABIC_DIGITS).replace(",", "")
    is_percentage = normalized.endswith(("%", "\u066a"))
    if is_percentage:
        normalized = normalized[:-1]
    try:
        return _NumericToken(Decimal(normalized), is_percentage=is_percentage)
    except InvalidOperation:
        return None


def _grounded_date_year(value: Any) -> Decimal | None:
    if isinstance(value, datetime):
        return Decimal(value.year)
    if isinstance(value, date):
        return Decimal(value.year)
    if not isinstance(value, str):
        return None
    try:
        if _ISO_DATE.fullmatch(value):
            return Decimal(date.fromisoformat(value).year)
        if _ISO_DATETIME.fullmatch(value):
            return Decimal(datetime.fromisoformat(value.replace("Z", "+00:00")).year)
    except ValueError:
        return None
    return None
