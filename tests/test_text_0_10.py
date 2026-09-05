"""``Text.*`` functions missing until this session's Microsoft Learn diff.

Found by fetching the real text-functions.md table and diffing it against
the live registry, per the task brief - not by memory. ``Character.*`` and
``Text.FromBinary`` were already implemented elsewhere in the registry
(``_misc.py`` / ``_connectors.py`` respectively) and are intentionally not
retested here; this file covers only what this session actually added:
``Text.Range``, ``Text.RemoveRange``, ``Text.ReplaceRange``,
``Text.ToBinary``, and the deliberate refusal ``Text.InferNumberType``.
"""

from __future__ import annotations

import base64

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Text.Range
# --------------------------------------------------------------------------


def test_text_range_matches_ms_docs_examples():
    # Text.Range("Hello World", 6) -> "World"
    assert evaluate('Text.Range("Hello World", 6)') == "World"
    # Text.Range("Hello World Hello", 6, 5) -> "World"
    assert evaluate('Text.Range("Hello World Hello", 6, 5)') == "World"


def test_text_range_null_propagates():
    assert evaluate("Text.Range(null, 0)") is None


def test_text_range_offset_at_end_with_no_count_is_empty():
    assert evaluate('Text.Range("hi", 2)') == ""


def test_text_range_negative_offset_is_eval_error():
    with pytest.raises(EvalError, match="offset must not be negative"):
        evaluate('Text.Range("hi", -1)')


def test_text_range_not_enough_characters_raises():
    # Trap (verified against the docs' own words: "Raises an error if
    # there aren't enough characters") - unlike Text.Middle, which clamps.
    with pytest.raises(EvalError, match="not enough characters"):
        evaluate('Text.Range("hi", 0, 5)')
    with pytest.raises(EvalError, match="not enough characters"):
        evaluate('Text.Range("hi", 5)')


def test_text_range_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate('Text.Range("hello", 0, -1)')


# --------------------------------------------------------------------------
# Text.RemoveRange
# --------------------------------------------------------------------------


def test_text_remove_range_matches_ms_docs_examples():
    assert evaluate('Text.RemoveRange("ABCDE", 2)') == "ABDE"
    assert evaluate('Text.RemoveRange("ABCDE", 1, 2)') == "ADE"


def test_text_remove_range_null_propagates():
    assert evaluate("Text.RemoveRange(null, 0)") is None


def test_text_remove_range_out_of_range_raises():
    # A choice (see the comment on the implementation): this docs page
    # gives no worked example for the boundary, so this pins the same
    # "raises" behaviour as sibling Text.Range, named as a choice, not a
    # verified fact for this specific function.
    with pytest.raises(EvalError, match="not enough characters"):
        evaluate('Text.RemoveRange("ABCDE", 3, 5)')


def test_text_remove_range_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate('Text.RemoveRange("ABCDE", 0, -1)')


# --------------------------------------------------------------------------
# Text.ReplaceRange
# --------------------------------------------------------------------------


def test_text_replace_range_matches_ms_docs_example():
    assert evaluate('Text.ReplaceRange("ABGF", 2, 1, "CDE")') == "ABCDEF"


def test_text_replace_range_null_propagates():
    assert evaluate('Text.ReplaceRange(null, 0, 0, "x")') is None


def test_text_replace_range_out_of_range_raises():
    with pytest.raises(EvalError, match="not enough characters"):
        evaluate('Text.ReplaceRange("ABGF", 2, 10, "x")')


def test_text_replace_range_count_is_required_not_optional():
    # Unlike RemoveRange, ReplaceRange's `count` is not optional in the
    # real signature - a 3-argument call is an arity error.
    with pytest.raises(UnsupportedError):
        evaluate('Text.ReplaceRange("ABGF", 2, 1)')


# --------------------------------------------------------------------------
# Text.ToBinary
# --------------------------------------------------------------------------


def test_text_to_binary_default_utf8_matches_ms_docs_example():
    # Text.ToBinary("Testing 1-2-3") base64-encodes to exactly
    # "VGVzdGluZyAxLTItMw==" per the docs' own worked example.
    result = evaluate('Text.ToBinary("Testing 1-2-3")')
    assert isinstance(result, bytes)
    assert base64.b64encode(result).decode() == "VGVzdGluZyAxLTItMw=="
    assert result == b"Testing 1-2-3"


def test_text_to_binary_utf16_with_bom_matches_ms_docs_example():
    # Text.ToBinary(text, 1200, true) - code page 1200 is TextEncoding.Utf16
    # (little-endian) per _connectors.py's own _CODE_PAGES table. Cross-
    # checked hex against the docs' own worked example:
    # "fffe540065007300740069006e006700200031002d0032002d003300".
    result = evaluate('Text.ToBinary("Testing 1-2-3", 1200, true)')
    assert result.hex() == "fffe540065007300740069006e006700200031002d0032002d003300"


def test_text_to_binary_null_propagates():
    assert evaluate("Text.ToBinary(null)") is None


def test_text_to_binary_unknown_code_page_is_unsupported():
    with pytest.raises(UnsupportedError, match="code page"):
        evaluate('Text.ToBinary("x", 99999)')


def test_text_to_binary_bom_refused_for_encoding_without_a_standard_bom():
    # cp1252 (code page 1252) has no standard byte-order mark - refuse
    # rather than guess one.
    with pytest.raises(UnsupportedError, match="byte-order mark"):
        evaluate('Text.ToBinary("x", 1252, true)')


# --------------------------------------------------------------------------
# Text.InferNumberType - deliberate refusal
# --------------------------------------------------------------------------


def test_text_infer_number_type_is_refused():
    # NEVER APPROXIMATE: the docs page for this function has no worked
    # example distinguishing Int64.Type from Double.Type/Decimal.Type/
    # Currency.Type/Percentage.Type, so this module refuses rather than
    # guess a subtype.
    with pytest.raises(UnsupportedError, match="Text.InferNumberType"):
        evaluate('Text.InferNumberType("3.14")')


def test_text_infer_number_type_refuses_non_invariant_culture_by_name():
    with pytest.raises(UnsupportedError, match="de-DE"):
        evaluate('Text.InferNumberType("3,14", "de-DE")')
