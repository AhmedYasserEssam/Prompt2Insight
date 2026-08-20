import pytest

from app.application.analytics.resolve_language import resolve_response_language
from app.domain.analytics.models import ResponseLanguage


def test_detects_arabic() -> None:
    assert resolve_response_language(
        "اعرض الإيرادات الشهرية",
        ResponseLanguage.AUTO,
    ) == "ar"


def test_detects_english() -> None:
    assert resolve_response_language(
        "Show monthly revenue",
        ResponseLanguage.AUTO,
    ) == "en"


@pytest.mark.parametrize(
    "question",
    ["وريني الإيرادات الشهرية", "عايز revenue by month"],
)
def test_detects_egyptian_and_code_switched_arabic(question: str) -> None:
    assert resolve_response_language(question, ResponseLanguage.AUTO) == "ar"


def test_explicit_language_wins() -> None:
    assert resolve_response_language(
        "Show monthly revenue",
        ResponseLanguage.ARABIC,
    ) == "ar"
