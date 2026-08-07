import re

_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u06D6-\u06ED]")
_WHITESPACE = re.compile(r"\s+")
_TRANSLATION = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ـ": ""}
)


def normalize_arabic_for_lookup(value: str) -> str:
    """Use only for alias lookup and evaluation, never for enterprise values."""
    value = value.translate(_TRANSLATION)
    value = _DIACRITICS.sub("", value)
    return _WHITESPACE.sub(" ", value).strip().casefold()
