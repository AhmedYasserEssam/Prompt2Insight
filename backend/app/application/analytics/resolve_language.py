import re

from app.domain.analytics.models import ResponseLanguage

_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def resolve_response_language(question: str, requested: ResponseLanguage) -> str:
    if requested is ResponseLanguage.ENGLISH:
        return "en"
    if requested is ResponseLanguage.ARABIC:
        return "ar"

    letters = [character for character in question if character.isalpha()]
    if not letters:
        return "en"

    return "ar" if any(_ARABIC.match(character) for character in letters) else "en"
