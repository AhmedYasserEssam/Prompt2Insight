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
    ("table", "language", "expected"),
    [
        (
            ResultTable(columns=["average_order_total"], rows=[[428.73]]),
            "en",
            "The average order total is 428.73.",
        ),
        (
            ResultTable(
                columns=["total_sales", "order_count"], rows=[[1250.5, 4]]
            ),
            "en",
            "The result is total sales: 1250.5; order count: 4.",
        ),
        (
            ResultTable(columns=["region", "revenue"], rows=[["Cairo", 10]]),
            "en",
            "The result is region: Cairo; revenue: 10.",
        ),
        (
            ResultTable(
                columns=["product_name", "total_sales"],
                rows=[["Desk", 300], ["Chair", 200], ["Lamp", 100]],
            ),
            "en",
            "The returned total sales ranking is: Desk — 300; Chair — 200; Lamp — 100.",
        ),
        (
            ResultTable(
                columns=["category", "total_sales"],
                rows=[["Technology", 300], ["Furniture", 200], ["Office Supplies", 100]],
            ),
            "en",
            "The total sales comparison is: Technology — 300; Furniture — 200; "
            "Office Supplies — 100.",
        ),
        (
            ResultTable(
                columns=["month", "revenue"],
                rows=[["2024-01", 10], ["2024-02", 30], ["2024-03", 20]],
            ),
            "en",
            "The results cover 2024-01 through 2024-03. The highest revenue is 30 in "
            "2024-02, and the lowest is 10 in 2024-01.",
        ),
        (
            ResultTable(
                columns=["customer", "city", "segment"],
                rows=[["A", "Cairo", "Retail"], ["B", "Giza", "Wholesale"]],
            ),
            "en",
            "The result contains 2 rows with customer, city, and segment.",
        ),
        (
            ResultTable(
                columns=["month", "total_sales"],
                rows=[["2024-01", 10], ["2024-02", 30], ["2024-03", 20]],
            ),
            "ar",
            "تغطي النتائج الفترة من 2024-01 إلى 2024-03. أعلى قيمة لـ إجمالي المبيعات هي "
            "30 في 2024-02، وأدنى قيمة هي 10 في 2024-01.",
        ),
        (
            ResultTable(columns=["average_order_total"], rows=[[428.73]]),
            "ar",
            "قيمة متوسط إجمالي الطلب هي 428.73.",
        ),
        (
            ResultTable(
                columns=["product_name", "total_sales"],
                rows=[["Desk", 300], ["Chair", 200], ["Lamp", 100]],
            ),
            "ar",
            "الترتيب حسب إجمالي المبيعات هو: Desk — 300؛ Chair — 200؛ Lamp — 100.",
        ),
        (
            ResultTable(columns=[], rows=[["unlabelled"]]),
            "en",
            "Query completed successfully. 1 row returned.",
        ),
        (
            ResultTable(columns=["region", "revenue"], rows=[]),
            "en",
            "No matching rows were returned.",
        ),
    ],
)
def test_deterministic_answer_summarizes_result_shape(
    table: ResultTable, language: str, expected: str
) -> None:
    assert deterministic_answer(table, language) == expected


def test_deterministic_answer_does_not_mutate_result_table() -> None:
    table = ResultTable(
        columns=["product_name", "total_sales"],
        rows=[["Desk", 300], ["Chair", 200], ["Lamp", 100]],
    )
    original = table.model_copy(deep=True)

    deterministic_answer(table, "en")

    assert table == original
