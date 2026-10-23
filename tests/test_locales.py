from string import Formatter

import pytest

from orderfix.locales import EN, HI, KN, TRANSLATIONS, tr


def fields(template):
    return {field for _, field, _, _ in Formatter().parse(template) if field is not None}


def test_every_language_has_complete_template_and_variable_parity():
    assert set(EN) == set(HI) == set(KN)
    for key, english in EN.items():
        for templates in (HI, KN):
            assert templates[key].strip()
            assert fields(english) == fields(templates[key]), key


@pytest.mark.parametrize("language", TRANSLATIONS)
def test_all_templates_render(language):
    for key, template in EN.items():
        values = {field: "TEST" for field in fields(template)}
        assert tr(language, key, **values)


@pytest.mark.parametrize("language", TRANSLATIONS)
def test_review_preserves_identifiers_and_exact_confirmation(language):
    result = tr(language, "review", token="a1b2c3", order="#1001", item="Headphones", quantity=2,
                reason=tr(language, "reason_damaged"), policy="POLICY")
    assert "confirm a1b2c3" in result
    assert "cancel a1b2c3" in result
    assert "#1001" in result
    assert "Headphones" in result
    assert "2" in result


def test_missing_template_or_variable_fails_loudly():
    with pytest.raises(KeyError):
        tr("en", "invented_status")
    with pytest.raises(KeyError):
        tr("hi", "review")
    assert tr("fr", "language_changed") == EN["language_changed"]


def test_languages_have_native_script_and_no_model_dependency():
    assert "हिंदी" in tr("hi", "language_changed")
    assert "ಕನ್ನಡ" in tr("kn", "language_changed")
    for language in TRANSLATIONS:
        assert tr(language, "status_other", status="NEW_BACKEND_STATUS") == "NEW_BACKEND_STATUS"
