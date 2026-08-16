import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnswerOutput,
    ChartSpecification,
    ResultTable,
)

_NUMBER = re.compile(
    r"(?<![\w.])-?[0-9\u0660-\u0669][0-9\u0660-\u0669,]*"
    r"(?:[.\u066b][0-9\u0660-\u0669]+)?(?:[eE][+-]?[0-9]+)?[%\u066a]?(?!\w)"
)
_ARABIC_DIGITS = {**{0x0660 + index: str(index) for index in range(10)}, 0x066B: "."}


@dataclass(frozen=True, slots=True)
class _NumericToken:
    value: Decimal
    is_percentage: bool = False


def validate_answer_output(
    output: AnswerOutput,
    table: ResultTable,
    *,
    request_context: str | None = None,
    execution_context: str | None = None,
) -> None:
    """Reject clear numeric fabrication without trying to formally prove natural prose."""
    if not output.answer.strip():
        raise Prompt2InsightError(
            ErrorCode.LLM_INVALID_OUTPUT,
            "The answer is empty.",
        )
    result_tokens: set[_NumericToken] = set()
    result_text_values: set[str] = set()
    for row in table.rows:
        for value in row:
            if (number := _as_numeric_token(value)) is not None:
                result_tokens.add(number)
            elif isinstance(value, str) and value:
                result_text_values.add(value)

    contextual_text = "\n".join(
        part
        for part in (
            request_context,
            execution_context,
        )
        if part
    )
    contextual_tokens = {
        token
        for match in _NUMBER.finditer(contextual_text)
        if (token := _as_numeric_token(match.group())) is not None
    }
    answer_text = "\n".join([output.answer, *output.insights, *output.warnings])
    grounded_text_spans = _find_exact_spans(answer_text, result_text_values)

    for match in _NUMBER.finditer(answer_text):
        token_span = _numeric_token_span(match.span(), answer_text)
        if _is_within_grounded_span(token_span, grounded_text_spans):
            continue
        raw_token = match.group()
        token = _as_numeric_token(raw_token)
        if token is None or token not in result_tokens | contextual_tokens:
            normalized = token.value if token is not None else None
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                f"The answer contains an ungrounded numeric value: {raw_token!r} "
                f"(normalized={normalized!s}).",
            )


def validate_chart_specification(
    chart: ChartSpecification | None, table: ResultTable
) -> None:
    """Keep chart grounding strict without coupling it to answer acceptance."""
    if chart is None:
        return
    references = [chart.x_column, *chart.y_columns]
    if any(column not in table.columns for column in references):
        raise Prompt2InsightError(
            ErrorCode.LLM_INVALID_OUTPUT,
            "The answer chart references columns outside the executed result schema.",
        )


def _numeric_token_span(token_span: tuple[int, int], text: str) -> tuple[int, int]:
    start, end = token_span
    while end > start and text[end - 1] in {",", "\u060c"}:
        end -= 1
    return start, end


def _find_exact_spans(text: str, values: set[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for value in values:
        start = 0
        while (start := text.find(value, start)) != -1:
            end = start + len(value)
            spans.append((start, end))
            start += 1
    return spans


def _is_within_grounded_span(
    token_span: tuple[int, int], grounded_spans: list[tuple[int, int]]
) -> bool:
    token_start, token_end = token_span
    return any(
        grounded_start <= token_start and token_end <= grounded_end
        for grounded_start, grounded_end in grounded_spans
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
