import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerGroundingContext, AnswerOutput, ResultTable

_NUMBER = re.compile(
    r"(?<![\w.])-?[0-9\u0660-\u0669][0-9\u0660-\u0669,]*"
    r"(?:[.\u066b][0-9\u0660-\u0669]+)?(?:[eE][+-]?[0-9]+)?[%\u066a]?(?!\w)"
)
_ARABIC_DIGITS = {**{0x0660 + index: str(index) for index in range(10)}, 0x066B: "."}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ].+$")
_REQUEST_CONTEXT_NUMBER = re.compile(
    r"\b(?:last|past)\s+[0-9\u0660-\u0669][0-9\u0660-\u0669,]*"
    r"\s+(?:day|week|month|year)s?\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _NumericToken:
    value: Decimal
    is_percentage: bool = False


def validate_answer_output(
    output: AnswerOutput,
    table: ResultTable,
    *,
    grounding_context: AnswerGroundingContext | None = None,
    request_context: str | None = None,
) -> None:
    """Ensure an answer can only refer to columns and numeric values actually returned."""
    if output.chart is not None:
        references = [output.chart.x_column, *output.chart.y_columns]
        if any(column not in table.columns for column in references):
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                "The answer chart references columns outside the executed result schema.",
            )

    result_tokens: set[_NumericToken] = set()
    result_text_values: set[str] = set()
    result_date_values: set[str] = set()
    for row in table.rows:
        for value in row:
            if (number := _as_numeric_token(value)) is not None:
                result_tokens.add(number)
            if (date_value := _as_grounded_date(value)) is not None:
                result_tokens.add(_NumericToken(Decimal(date_value.year)))
                result_date_values.update(_grounded_date_renderings(value, date_value))
            if (text_value := _as_grounded_text_value(value)) is not None:
                result_text_values.add(text_value)
    text = "\n".join(
        [
            output.answer,
            *output.insights,
            *output.warnings,
            *([output.chart.title] if output.chart is not None else []),
        ]
    )
    grounded_text_spans = _find_exact_spans(text, result_text_values)
    grounded_date_spans = _find_exact_spans(text, result_date_values)
    grounded_context_spans = _context_grounded_spans(text, grounding_context)
    grounded_request_spans = _request_context_spans(text, request_context)
    grounded_spans = [
        *grounded_text_spans,
        *grounded_date_spans,
        *grounded_context_spans,
        *grounded_request_spans,
    ]
    for match in _NUMBER.finditer(text):
        token_span = _numeric_token_span(match.span(), text)
        if _is_within_grounded_span(token_span, grounded_spans):
            continue
        raw_token = match.group()
        token = _as_numeric_token(raw_token)
        if token is None or token not in result_tokens:
            normalized = token.value if token is not None else None
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                f"The answer contains an ungrounded numeric value: {raw_token!r} "
                f"(normalized={normalized!s}).",
            )


def _numeric_token_span(token_span: tuple[int, int], text: str) -> tuple[int, int]:
    start, end = token_span
    while end > start and text[end - 1] in {",", "\u060c"}:
        end -= 1
    return start, end


def _context_grounded_spans(
    text: str, context: AnswerGroundingContext | None
) -> list[tuple[int, int]]:
    if context is None:
        return []
    spans: list[tuple[int, int]] = []
    for date_range in context.date_ranges:
        for year in range(
            date.fromisoformat(date_range.start).year,
            date.fromisoformat(date_range.end).year + 1,
        ):
            spans.extend(_find_pattern_spans(text, rf"(?<!\d){year}(?!\d)"))
    if context.top_n is not None:
        spans.extend(
            _find_pattern_spans(
                text, rf"(?:\btop\s+{context.top_n}\b|(?:أعلى|أفضل)\s+{context.top_n})"
            )
        )
    for numeric_filter in context.numeric_filters:
        value = re.escape(str(numeric_filter.value))
        field = re.escape(numeric_filter.field.replace("_", " "))
        spans.extend(
            _find_pattern_spans(
                text,
                rf"(?:\b{field}\b\s*(?:=|is|of|at least|more than|greater than|over|above|"
                rf"less than|under|below)\s*{value}\b|(?:at least|more than|greater than|"
                rf"over|above|less than|under|below|أكثر من|أكبر من|على الأقل|لا يقل عن|أقل من|دون)"
                rf"\s+{value}\b)",
            )
        )
    return spans


def _request_context_spans(text: str, request_context: str | None) -> list[tuple[int, int]]:
    """Allow relative time-window phrasing copied from the user's request."""
    if request_context is None:
        return []
    phrases = {
        match.group()
        for match in _REQUEST_CONTEXT_NUMBER.finditer(request_context)
    }
    return _find_exact_spans_case_insensitive(text, phrases)


def _find_pattern_spans(text: str, pattern: str) -> list[tuple[int, int]]:
    return [match.span() for match in re.finditer(pattern, text, flags=re.IGNORECASE)]


def _as_grounded_text_value(value: Any) -> str | None:
    if not isinstance(value, str) or not any(character.isalpha() for character in value):
        return None
    if _as_numeric_token(value) is not None or _as_grounded_date(value) is not None:
        return None
    return value


def _find_exact_spans(text: str, values: set[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for value in values:
        start = 0
        while (start := text.find(value, start)) != -1:
            end = start + len(value)
            spans.append((start, end))
            start += 1
    return spans


def _find_exact_spans_case_insensitive(text: str, values: set[str]) -> list[tuple[int, int]]:
    lowered_text = text.casefold()
    spans: list[tuple[int, int]] = []
    for value in values:
        start = 0
        lowered_value = value.casefold()
        while (start := lowered_text.find(lowered_value, start)) != -1:
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


def _as_grounded_date(value: Any) -> date | datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        if _ISO_DATE.fullmatch(value):
            return date.fromisoformat(value)
        if _ISO_DATETIME.fullmatch(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return None


def _grounded_date_renderings(
    source_value: Any, date_value: date | datetime
) -> set[str]:
    renderings = {source_value} if isinstance(source_value, str) else set()
    if not isinstance(date_value, datetime):
        renderings.add(date_value.isoformat())
        return renderings

    iso_datetime = date_value.isoformat()
    renderings.update(
        {
            iso_datetime,
            date_value.isoformat(sep=" "),
            date_value.date().isoformat(),
        }
    )
    if iso_datetime.endswith("+00:00"):
        renderings.add(f"{iso_datetime[:-6]}Z")
    return renderings
