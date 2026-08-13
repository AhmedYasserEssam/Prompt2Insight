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
