import pytest

from app.application.analytics.answer_fallback import deterministic_answer
from app.application.analytics.answer_validation import (
    validate_answer_output,
    validate_chart_specification,
)
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerOutput, ChartSpecification, ResultTable


def test_accepts_analytical_values_grounded_in_result_table() -> None:
    validate_answer_output(
        AnswerOutput(answer="Cairo generated 10 in revenue."),
        ResultTable(columns=["region", "revenue"], rows=[["Cairo", 10]]),
    )


def test_rejects_clearly_fabricated_numeric_analytical_value() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="Cairo generated 11 in revenue."),
            ResultTable(columns=["region", "revenue"], rows=[["Cairo", 10]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT
    assert "normalized=11" in raised.value.message


def test_rejects_empty_generated_answer() -> None:
    with pytest.raises(Prompt2InsightError) as raised:
        validate_answer_output(
            AnswerOutput(answer="  "),
            ResultTable(columns=["revenue"], rows=[[10]]),
        )

    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        (
            "Show top selling product in 2016 in Houston",
            "In 2016, X had the highest sales at 12,345.67.",
        ),
        (
            "اعرض أعلى منتج مبيعاً في هيوستن سنة ٢٠١٦",
            "في سنة ٢٠١٦ حقق X أعلى مبيعات بقيمة ١٢٣٤٥\u066b\u0666\u0667.",
        ),
    ],
)
def test_user_context_numbers_are_allowed_without_special_case_validation(
    question: str, answer: str
) -> None:
    validate_answer_output(
        AnswerOutput(answer=answer),
        ResultTable(columns=["product_name", "total_sales"], rows=[["X", 12345.67]]),
        request_context=question,
    )


def test_execution_filter_context_is_allowed_but_unrelated_numbers_are_rejected() -> None:
    table = ResultTable(columns=["total_sales"], rows=[[5000]])
    validate_answer_output(
        AnswerOutput(answer="For sales over 1000, total sales were 5000."),
        table,
        execution_context='{"parameters":[{"value":"1000"}]}',
    )

    with pytest.raises(Prompt2InsightError):
        validate_answer_output(
            AnswerOutput(answer="Total sales were 999999."),
            table,
            execution_context='{"parameters":[{"value":"1000"}]}',
        )


def test_exact_returned_entity_text_grounds_embedded_numbers() -> None:
    validate_answer_output(
        AnswerOutput(
            answer="Canon imageCLASS 2200 Advanced Copier had the highest sales at 61,599.824."
        ),
        ResultTable(
            columns=["product_name", "total_sales"],
            rows=[["Canon imageCLASS 2200 Advanced Copier", 61599.824]],
        ),
    )


def test_numeric_result_does_not_ground_an_invented_percentage_claim() -> None:
    with pytest.raises(Prompt2InsightError):
        validate_answer_output(
            AnswerOutput(answer="Revenue increased by 10%."),
            ResultTable(columns=["revenue"], rows=[[10]]),
        )


def test_chart_validation_is_independent_and_strict() -> None:
    table = ResultTable(columns=["region", "revenue"], rows=[["Cairo", 10]])
    validate_chart_specification(
        ChartSpecification(
            chart_type="bar", x_column="region", y_columns=["revenue"], title="Revenue"
        ),
        table,
    )

    with pytest.raises(Prompt2InsightError) as raised:
        validate_chart_specification(
            ChartSpecification(
                chart_type="bar", x_column="month", y_columns=["revenue"], title="Revenue"
            ),
            table,
        )
    assert raised.value.code is ErrorCode.LLM_INVALID_OUTPUT


@pytest.mark.parametrize(
    ("rows", "language", "expected"),
    [
        ([["Cairo", 10]], "en", "Query completed successfully. 1 row returned."),
        (
            [["Cairo", 10], ["Giza", 20]],
            "en",
            "Query completed successfully. 2 rows returned.",
        ),
        ([["Cairo", 10]], "ar", "تم تنفيذ الاستعلام بنجاح. تم إرجاع 1 صفوف."),
        ([], "en", "No matching rows were returned."),
    ],
)
def test_deterministic_answer_uses_only_query_result_row_count(
    rows: list[list[object]], language: str, expected: str
) -> None:
    table = ResultTable(columns=["region", "revenue"], rows=rows)

    assert deterministic_answer(table, language) == expected
