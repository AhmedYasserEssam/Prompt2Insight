import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerOutput, ResultTable

_NUMBER = re.compile(
    r"(?<![\w.])-?[0-9\u0660-\u0669][0-9\u0660-\u0669,]*"
    r"(?:[.\u066b][0-9\u0660-\u0669]+)?(?:[eE][+-]?[0-9]+)?%?(?!\w)"
)
_ARABIC_DIGITS = {**{0x0660 + index: str(index) for index in range(10)}, 0x066B: "."}


def validate_answer_output(output: AnswerOutput, table: ResultTable) -> None:
    """Ensure an answer can only refer to columns and numeric values actually returned."""
    if output.chart is not None:
        references = [output.chart.x_column, *output.chart.y_columns]
        if any(column not in table.columns for column in references):
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                "The answer chart references columns outside the executed result schema.",
            )

    result_numbers = {
        _as_decimal(value)
        for row in table.rows
        for value in row
        if _as_decimal(value) is not None
    }
    text = "\n".join(
        [
            output.answer,
            *output.insights,
            *output.warnings,
            *([output.chart.title] if output.chart is not None else []),
        ]
    )
    for match in _NUMBER.findall(text):
        value = _as_decimal(match)
        if value is None or value not in result_numbers:
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                "The answer contains a numeric value that was not returned by the query.",
            )


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    normalized = value.translate(_ARABIC_DIGITS).replace(",", "").rstrip("%")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None
