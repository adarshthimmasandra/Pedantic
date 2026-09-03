"""Input and output sanitization."""

from __future__ import annotations

from clipai import cleaning


def test_sanitize_input_normalizes_line_endings_and_whitespace():
    assert cleaning.sanitize_input("a\r\nb\rc") == "a\nb\nc"
    assert cleaning.sanitize_input("one   \ntwo\t\nthree") == "one\ntwo\nthree"
    # Long runs of blank lines are collapsed but paragraph breaks survive.
    assert cleaning.sanitize_input("a\n\n\n\n\n\nb") == "a\n\n\nb"
    assert cleaning.sanitize_input("  padded  ") == "padded"
    assert cleaning.sanitize_input("   \n\t  ") == ""
    assert cleaning.sanitize_input("") == ""


def test_sanitize_input_removes_invisible_and_control_characters():
    # A zero-width space and a BOM are invisible but would still cost tokens.
    assert cleaning.sanitize_input("he\u200bllo\ufeff") == "hello"
    assert cleaning.sanitize_input("a\tb\x00\x07c") == "a\tbc"
    assert cleaning.sanitize_input("line\u2028break") == "line\nbreak"


def test_has_transformable_text_requires_a_letter_or_digit():
    assert cleaning.has_transformable_text("hello") is True
    assert cleaning.has_transformable_text("7") is True
    assert cleaning.has_transformable_text("  -- ,. ") is False
    assert cleaning.has_transformable_text("") is False


def test_clean_output_unwraps_fences_preambles_and_quotes():
    assert cleaning.clean_output("```text\nFixed sentence.\n```") == "Fixed sentence."
    assert (
        cleaning.clean_output("Sure! Here's the corrected text:\nThe meeting is Tuesday.")
        == "The meeting is Tuesday."
    )
    assert cleaning.clean_output('"Fixed sentence."') == "Fixed sentence."
    assert cleaning.clean_output("\u201cFixed sentence.\u201d") == "Fixed sentence."
    assert cleaning.clean_output("   \n ") == ""


def test_clean_output_preserves_text_that_only_looks_wrapped():
    # The response is not entirely quoted, so nothing may be stripped.
    internal = 'He said "yes" to the plan.'
    assert cleaning.clean_output(internal) == internal
    # Quoted, but with inner quotes, so the outer pair is ambiguous.
    ambiguous = '"He said "yes" to the plan."'
    assert cleaning.clean_output(ambiguous) == ambiguous
    # A sentence that merely begins like a preamble keeps its text.
    assert cleaning.clean_output("Here is the report I promised.") == (
        "Here is the report I promised."
    )


def test_preview_is_single_line_and_bounded():
    assert cleaning.preview("a\n\nb   c") == "a b c"
    truncated = cleaning.preview("x" * 200, limit=10)
    assert len(truncated) == 10
    assert truncated.endswith("\u2026")
