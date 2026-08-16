from datetime import date, datetime

import pytest

from app.application.analytics.answer_validation import validate_answer_output
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnswerGroundingContext,
    AnswerOutput,
    ChartSpecification,
    DateRangeGrounding,
    NumericFilterGrounding,
    ResultTable,
)


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


def test_date_range_context_grounds_standalone_years_without_grounding_invented_metrics() -> None:
    context = AnswerGroundingContext(
        date_ranges=[DateRangeGrounding(start="2015-01-01", end="2016-12-31")]
    )
    table = ResultTable(
        columns=["product_name", "total_sales"],
        rows=[["Cisco TelePresence System EX90 Videoconferencing Unit", 22638.48]],
    )

    validate_answer_output(
        AnswerOutput(
            answer=(
                "In 2016, Cisco TelePresence System EX90 Videoconferencing Unit "
                "had the highest sales at 22,638.48."
            )
        ),
        table,
        grounding_context=context,
    )

    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="In 2016, total sales were 999999."),
            table,
            grounding_context=context,
        )
    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT


def test_request_context_grounds_relative_time_window_but_not_fabricated_result() -> None:
    table = ResultTable(columns=["total_sales"], rows=[[22638.48]])

    validate_answer_output(
        AnswerOutput(answer="Over the last 3 months, sales were 22,638.48."),
        table,
        request_context="What were sales in the last 3 months?",
    )

    with pytest.raises(Prompt2InsightError):
        validate_answer_output(
            AnswerOutput(answer="Over the last 3 months, sales were 99,999."),
            table,
            request_context="What were sales in the last 3 months?",
        )


@pytest.mark.parametrize(
    "question",
    [
        "Show top selling product in 2016 in Houston",
        "اعرض المنتج الأكثر مبيعاً في 2016 في هيوستن",
        "اعرض top selling product في 2016 في Houston",
    ],
)
def test_request_context_grounds_matching_year(question: str) -> None:
    validate_answer_output(
        AnswerOutput(answer="In 2016, X had sales of 12345.67."),
        ResultTable(columns=["product_name", "total_sales"], rows=[["X", 12345.67]]),
        request_context=question,
    )


def test_request_context_year_does_not_ground_fabricated_result_number() -> None:
    with pytest.raises(Prompt2InsightError):
        validate_answer_output(
            AnswerOutput(answer="Sales were 999999."),
            ResultTable(columns=["total_sales"], rows=[[12345.67]]),
            request_context="Show top selling product in 2016",
        )


def test_request_context_does_not_ground_a_different_year() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="In 2017, X had sales of 12345.67."),
            ResultTable(columns=["product_name", "total_sales"], rows=[["X", 12345.67]]),
            request_context="Show top selling product in 2016",
        )

    assert "'2017," in raised.value.message


def test_top_n_context_grounds_only_a_top_n_phrase() -> None:
    context = AnswerGroundingContext(top_n=5)
    table = ResultTable(columns=["product_name"], rows=[["Cisco TelePresence"]])

    validate_answer_output(
        AnswerOutput(answer="The top 5 products include Cisco TelePresence."),
        table,
        grounding_context=context,
    )

    with pytest.raises(Prompt2InsightError):
        validate_answer_output(
            AnswerOutput(answer="Cisco TelePresence generated 5 in sales."),
            table,
            grounding_context=context,
        )


def test_numeric_filter_context_requires_contextual_filter_wording() -> None:
    context = AnswerGroundingContext(
        numeric_filters=[NumericFilterGrounding(field="sales", operator="gt", value=1000)]
    )
    table = ResultTable(columns=["total_sales"], rows=[[5000]])

    validate_answer_output(
        AnswerOutput(answer="For sales over 1000, total sales were 5000."),
        table,
        grounding_context=context,
    )

    with pytest.raises(Prompt2InsightError):
        validate_answer_output(
            AnswerOutput(answer="Total sales were 1000."),
            table,
            grounding_context=context,
        )


@pytest.mark.parametrize(
    "answer",
    [
        "من 2015 إلى 2016، بلغت المبيعات 22638.48.",
        "From 2015 to 2016, بلغت المبيعات 22638.48.",
    ],
)
def test_arabic_and_code_switched_date_context_grounds_years(answer: str) -> None:
    validate_answer_output(
        AnswerOutput(answer=answer),
        ResultTable(columns=["total_sales"], rows=[[22638.48]]),
        grounding_context=AnswerGroundingContext(
            date_ranges=[DateRangeGrounding(start="2015-01-01", end="2016-12-31")]
        ),
    )


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


@pytest.mark.parametrize(
    ("returned_date", "rendered_date"),
    [
        ("2015-01-01", "2015-01-01"),
        ("2015-01-01T00:00:00", "2015-01-01T00:00:00"),
        ("2015-01-01T00:00:00", "2015-01-01"),
        ("2015-01-01 00:00:00", "2015-01-01 00:00:00"),
        (date(2015, 1, 1), "2015-01-01"),
        (datetime(2015, 1, 1), "2015-01-01T00:00:00"),
        (datetime(2015, 1, 1), "2015-01-01"),
    ],
)
def test_returned_date_grounds_a_supported_complete_date_span(
    returned_date: object, rendered_date: str
) -> None:
    validate_answer_output(
        AnswerOutput(answer=f"Revenue was reported for {rendered_date}."),
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


@pytest.mark.parametrize(
    ("returned_date", "standalone_component"),
    [("2015-01-01", "01"), ("2015-01-03", "03")],
)
def test_returned_date_does_not_ground_standalone_month_or_day_tokens(
    returned_date: str, standalone_component: str
) -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer=f"There were {standalone_component} products."),
            ResultTable(columns=["order_month"], rows=[[returned_date]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT
    assert repr(standalone_component) in raised.value.message


def test_arbitrary_result_string_does_not_ground_embedded_number() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="Product number 123."),
            ResultTable(columns=["product"], rows=[["Product 123"]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT


def test_exact_returned_text_grounds_its_embedded_integer_occurrence() -> None:
    validate_answer_output(
        AnswerOutput(
            answer=(
                "Canon imageCLASS 2200 Advanced Copier has the highest sales at 61,599.824."
            )
        ),
        ResultTable(
            columns=["product_name", "total_sales"],
            rows=[["Canon imageCLASS 2200 Advanced Copier", 61599.824]],
        ),
    )


def test_exact_returned_text_grounds_its_embedded_decimal_occurrence() -> None:
    validate_answer_output(
        AnswerOutput(answer="Samsung Galaxy Mega 6.3 had the highest sales."),
        ResultTable(columns=["product_name"], rows=[["Samsung Galaxy Mega 6.3"]]),
    )


def test_multiple_exact_returned_text_values_ground_embedded_numbers() -> None:
    validate_answer_output(
        AnswerOutput(
            answer=(
                "HON 5400 Series Task Chairs for Big and Tall ranked above "
                "Samsung Galaxy Mega 6.3."
            )
        ),
        ResultTable(
            columns=["product_name"],
            rows=[
                ["HON 5400 Series Task Chairs for Big and Tall"],
                ["Samsung Galaxy Mega 6.3"],
            ],
        ),
    )


def test_second_standalone_occurrence_of_embedded_number_is_rejected() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(
                answer="Canon imageCLASS 2200 Advanced Copier had 2200 orders."
            ),
            ResultTable(
                columns=["product_name"],
                rows=[["Canon imageCLASS 2200 Advanced Copier"]],
            ),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT
    assert "'2200'" in raised.value.message
    assert "normalized=2200" in raised.value.message


def test_arbitrary_number_outside_exact_returned_text_is_rejected() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="The product achieved 9999 sales."),
            ResultTable(
                columns=["product_name"],
                rows=[["Canon imageCLASS 2200 Advanced Copier"]],
            ),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT
    assert "'9999'" in raised.value.message


def test_unreturned_text_with_embedded_number_is_rejected() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="Product XYZ 3000 had the highest sales."),
            ResultTable(
                columns=["product_name"],
                rows=[["Canon imageCLASS 2200 Advanced Copier"]],
            ),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT
    assert "'3000'" in raised.value.message


def test_pure_numeric_result_string_uses_numeric_grounding() -> None:
    validate_answer_output(
        AnswerOutput(answer="There were 2,200 orders."),
        ResultTable(columns=["order_count"], rows=[["2200"]]),
    )


def test_exact_arabic_text_grounds_its_embedded_digit_occurrence() -> None:
    validate_answer_output(
        AnswerOutput(answer="طابعة كانون ٢٢٠٠ المتقدمة حققت أعلى المبيعات."),
        ResultTable(columns=["product_name"], rows=[["طابعة كانون ٢٢٠٠ المتقدمة"]]),
    )


@pytest.mark.parametrize("returned_value", [10, "10"])
@pytest.mark.parametrize("percentage", ["10%", "\u0661\u0660\u066a"])
def test_numeric_result_does_not_ground_percentage_token(
    returned_value: object, percentage: str
) -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer=f"Revenue increased by {percentage}."),
            ResultTable(columns=["revenue"], rows=[[returned_value]]),
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
