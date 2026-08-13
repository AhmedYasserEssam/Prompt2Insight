from datetime import date, datetime

import pytest

from app.application.analytics.answer_validation import validate_answer_output
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerOutput, ChartSpecification, ResultTable


def test_accepts_answer_and_chart_references_grounded_in_result_table() -> None:
    output = AnswerOutput(
        answer="Cairo generated 10 in revenue.",
        chart=ChartSpecification(
            chart_type="bar", x_column="region", y_columns=["revenue"], title="Revenue"
        ),
    )

    validate_answer_output(output, ResultTable(columns=["region", "revenue"], rows=[["Cairo", 10]]))


def test_rejects_invented_numeric_values() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="Cairo generated 11 in revenue."),
            ResultTable(columns=["region", "revenue"], rows=[["Cairo", 10]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT


def test_rejects_ungrounded_numeric_token_with_internal_detail() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="There are 48 monthly observations."),
            ResultTable(columns=["revenue"], rows=[[100], [200]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT
    assert "'48'" in raised.value.message
    assert "normalized=48" in raised.value.message


def test_accepts_exact_decimal_value() -> None:
    validate_answer_output(
        AnswerOutput(answer="Revenue was 1234.50."),
        ResultTable(columns=["revenue"], rows=[["1234.50"]]),
    )


def test_accepts_arabic_digits_for_exact_returned_value() -> None:
    validate_answer_output(
        AnswerOutput(answer="الإيرادات كانت \u0661\u0662\u0663\u0664\u066b\u0665\u0660."),
        ResultTable(columns=["revenue"], rows=[["1234.50"]]),
    )


@pytest.mark.parametrize(
    "returned_date",
    [
        date(2015, 1, 1),
        datetime(2015, 1, 1),
        "2015-01-01",
        "2015-01-01T00:00:00",
    ],
)
def test_returned_date_grounds_its_year(returned_date: object) -> None:
    validate_answer_output(
        AnswerOutput(answer="Revenue starts in 2015."),
        ResultTable(columns=["order_month"], rows=[[returned_date]]),
    )


@pytest.mark.parametrize("ungrounded", ["48", "12", "3"])
def test_returned_date_does_not_ground_derived_count_month_or_day(ungrounded: str) -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer=f"The result contains {ungrounded}."),
            ResultTable(columns=["order_month"], rows=[["2015-12-03"]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT


def test_arbitrary_result_string_does_not_ground_embedded_number() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="Product number 123."),
            ResultTable(columns=["product"], rows=[["Product 123"]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT


@pytest.mark.parametrize("percentage", ["10%", "\u0661\u0660\u066a"])
def test_numeric_result_does_not_ground_percentage_token(percentage: str) -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer=f"Revenue increased by {percentage}."),
            ResultTable(columns=["revenue"], rows=[[10]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT
    assert repr(percentage) in raised.value.message


def test_explicit_percentage_result_grounds_percentage_token() -> None:
    validate_answer_output(
        AnswerOutput(answer="Revenue increased by 10%."),
        ResultTable(columns=["growth"], rows=[["10%"]]),
    )


@pytest.mark.parametrize("answer", ["Revenue was 1e9.", "الإيرادات كانت \u0661\u0661."])
def test_rejects_invented_english_and_arabic_numeric_notation(answer: str) -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer=answer),
            ResultTable(columns=["region", "revenue"], rows=[["Cairo", 10]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT


def test_rejects_chart_columns_not_present_in_the_result_schema() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(
                answer="Revenue was 10.",
                chart=ChartSpecification(
                    chart_type="bar", x_column="month", y_columns=["revenue"], title="Revenue"
                ),
            ),
            ResultTable(columns=["region", "revenue"], rows=[["Cairo", 10]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT
