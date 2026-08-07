from app.infrastructure.i18n.arabic import normalize_arabic_for_lookup


def test_normalizes_alias() -> None:
    assert normalize_arabic_for_lookup("إِجْمَالِي  الإِيرَادَات") == "اجمالي الايرادات"
