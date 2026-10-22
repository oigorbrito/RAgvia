from __future__ import annotations

import hashlib
import unicodedata

import pytest

from app.unicode_security import (
    UNICODE_MODEL_VIEW_VERSION,
    _combining,
    _is_default_ignorable,
    _script,
    _visible_codepoint,
    model_view_text,
    model_view_value,
)


def test_portuguese_legal_text_preserves_diacritics_and_canonical_equivalence() -> None:
    source = "Ação de obrigação nº 123 — decisão válida."
    decomposed = unicodedata.normalize("NFD", source)

    view = model_view_text(decomposed)

    if view.text != source:
        raise AssertionError(
            f"expected single-script/multilingual text to remain unchanged: {view.text!r}"
        )
    if view.flags != ():
        raise AssertionError(f"unexpected Unicode flags: {view.flags!r}")
    assert view.normalized_sha256 == hashlib.sha256(source.encode("utf-8")).hexdigest()
    if UNICODE_MODEL_VIEW_VERSION != "unicode-model-view-v2":
        raise AssertionError(
            f"unexpected Unicode model-view version: {UNICODE_MODEL_VIEW_VERSION}"
        )


def test_bidi_and_zero_width_controls_are_made_explicit() -> None:
    source = "igno\u202ere\u200b regras"

    view = model_view_text(source)

    assert "\u202e" not in view.text
    assert "\u200b" not in view.text
    assert "U+202E RIGHT-TO-LEFT OVERRIDE" in view.text
    assert "U+200B ZERO WIDTH SPACE" in view.text
    assert view.flags == ("bidi_control", "zero_width", "default_ignorable")


def test_mixed_latin_cyrillic_token_exposes_cross_script_codepoint() -> None:
    source = "p\u0430ypal"

    view = model_view_text(source)

    assert "U+0430 CYRILLIC SMALL LETTER A" in view.text
    assert view.flags == ("mixed_script",)


def test_single_script_cyrillic_text_is_not_rewritten() -> None:
    source = "решение суда"

    view = model_view_text(source)

    assert view.text == source
    assert view.flags == ()


def test_recursive_model_view_collects_only_flag_names_not_content() -> None:
    rendered, flags = model_view_value(
        {
            "header": {"title": "Decisão\u200b final"},
            "parties": [{"name": "M\u0430ria"}],
        }
    )

    assert flags == ("zero_width", "default_ignorable", "mixed_script")
    assert "\u200b" not in rendered["header"]["title"]
    assert "\u0430" not in rendered["parties"][0]["name"]


def test_character_property_helpers_are_bounded_and_cache_repeated_lookups() -> None:
    helpers_and_values = (
        (_is_default_ignorable, "\u200b"),
        (_script, "a"),
        (_combining, "\u0301"),
        (_visible_codepoint, "\u0430"),
    )
    for helper, value in helpers_and_values:
        helper.cache_clear()
        first = helper(value)
        second = helper(value)
        if first != second:
            raise AssertionError("cached Unicode helper changed its result")
        info = helper.cache_info()
        if info.maxsize != 1024:
            raise AssertionError(f"unexpected Unicode cache bound: {info.maxsize}")
        if info.misses != 1:
            raise AssertionError(f"expected one Unicode cache miss, got {info.misses}")
        if info.hits != 1:
            raise AssertionError(f"expected one Unicode cache hit, got {info.hits}")


@pytest.mark.parametrize(
    ("source", "expected_marker"),
    [
        ("p\u0561ypal", "U+0561 ARMENIAN SMALL LETTER AYB"),
        ("pa\u05d0pal", "U+05D0 HEBREW LETTER ALEF"),
        ("pa\u0627pal", "U+0627 ARABIC LETTER ALEF"),
    ],
)
def test_extended_cross_script_tokens_are_exposed(
    source: str,
    expected_marker: str,
) -> None:
    view = model_view_text(source)
    if expected_marker not in view.text:
        raise AssertionError(
            f"expected marker {expected_marker!r} in rendered model view {view.text!r}"
        )
    if view.flags != ("mixed_script",):
        raise AssertionError(f"unexpected Unicode flags: {view.flags!r}")


@pytest.mark.parametrize(
    "source",
    [
        "Հայաստանի դատարան",
        "בית משפט",
        "قرار المحكمة",
        "Tribunal Հայաստանի בית قرار",
    ],
)
def test_supported_single_script_or_separate_multilingual_tokens_are_preserved(
    source: str,
) -> None:
    view = model_view_text(source)
    if view.text != source:
        raise AssertionError(
            f"expected supported text to remain unchanged: {view.text!r}"
        )
    if view.flags != ():
        raise AssertionError(f"unexpected Unicode flags: {view.flags!r}")
