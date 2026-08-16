from app.domain.analytics.models import ResultTable


def deterministic_answer(table: ResultTable, language: str) -> str:
    """Describe execution only; business interpretation remains in the result table."""
    row_count = len(table.rows)
    if row_count == 0:
        return (
            "لم يتم إرجاع صفوف مطابقة."
            if language == "ar"
            else "No matching rows were returned."
        )
    if language == "ar":
        return f"تم تنفيذ الاستعلام بنجاح. تم إرجاع {row_count} صفوف."
    noun = "row" if row_count == 1 else "rows"
    return f"Query completed successfully. {row_count} {noun} returned."
