"""Tests for the culture / number-format / date-time-format surface added to
`src/pqtools/builtins/_number.py` and `src/pqtools/builtins/_datetime.py`.

Every expected string in this file is either derived from Python's own
verified computation (e.g. `math.fmod`, `decimal.Decimal`) or copied
verbatim from Microsoft's own "Standard numeric format strings" /
"Standard date and time format strings" / `Number.ToText` docs - never
hand-typed from memory. Where a value is taken from the docs, the comment
says so, so a future reader can re-verify against the source.

Kept refusals (deliberately still `UnsupportedError`) are pinned here too,
not just the newly-supported behaviour, per the task brief.
"""

from __future__ import annotations

import decimal
import math

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Number.ToText - standard numeric format strings
#
# Every non-culture expected value below is copied verbatim from
# learn.microsoft.com/en-us/dotnet/standard/base-types/
# standard-numeric-format-strings (fetched during this task; see the
# report for the exact examples used).
# --------------------------------------------------------------------------


def test_number_to_text_decimal_format():
    assert evaluate('Number.ToText(1234, "D")') == "1234"
    # Doc example: -1234 ("D6") -> -001234.
    assert evaluate('Number.ToText(-1234, "D6")') == "-001234"


def test_number_to_text_decimal_format_requires_whole_number():
    with pytest.raises(EvalError, match="whole number"):
        evaluate('Number.ToText(1.5, "D")')


def test_number_to_text_fixed_format_unchanged():
    # Pre-existing behaviour (tests/test_builtins_scalar.py already pins
    # this one) - re-asserted here as the baseline the other formats sit
    # next to, not a new behaviour.
    assert evaluate('Number.ToText(1.5, "F2")') == "1.50"
    assert evaluate('Number.ToText(4, "F")') == "4.00"


def test_number_to_text_number_format_groups_thousands():
    # Doc example: 1234.567 ("N", en-US) -> 1,234.57.
    assert evaluate('Number.ToText(1234.567, "N")') == "1,234.57"
    # Doc example: -1234.56 ("N3", en-US) -> -1,234.560.
    assert evaluate('Number.ToText(-1234.56, "N3")') == "-1,234.560"


def test_number_to_text_scientific_format_uses_three_digit_exponent():
    # Doc example: 1052.0329112756 ("E", en-US) -> 1.052033E+003. Default
    # precision is 6 - confirmed by the doc's own "Default precision
    # specifier: 6" line, and this is exactly that no-digits case.
    assert evaluate('Number.ToText(1052.0329112756, "E")') == "1.052033E+003"
    assert evaluate('Number.ToText(-1052.0329112756, "E2")') == "-1.05E+003"


def test_number_to_text_percent_format_has_space_before_symbol():
    # Doc example: 1 ("P", en-US) -> 100.00 %. The space is real - it is a
    # documented .NET default, not a typo, and callers relying on "no
    # space" are the ones surprised by real Power Query here.
    assert evaluate('Number.ToText(1, "P")') == "100.00 %"
    # Doc example: -0.39678 ("P1", en-US) -> -39.7 %.
    assert evaluate('Number.ToText(-0.39678, "P1")') == "-39.7 %"


def test_number_to_text_currency_format_positive():
    # Doc example: 123.456 ("C", en-US) -> $123.46.
    assert evaluate('Number.ToText(123.456, "C")') == "$123.46"


def test_number_to_text_currency_format_negative_uses_parentheses():
    # Doc example: -123.456 ("C3", en-US) -> ($123.456). en-US's default
    # CurrencyNegativePattern really is parentheses, not a leading minus -
    # verified against the doc's own worked example rather than assumed.
    assert evaluate('Number.ToText(-123.456, "C3")') == "($123.456)"


def test_number_to_text_hex_format():
    assert evaluate('Number.ToText(255, "X")') == "FF"
    assert evaluate('Number.ToText(255, "X4")') == "00FF"
    assert evaluate('Number.ToText(0, "X")') == "0"


def test_number_to_text_hex_format_requires_whole_number():
    with pytest.raises(EvalError, match="whole number"):
        evaluate('Number.ToText(1.5, "X")')


def test_number_to_text_hex_format_rejects_negative():
    # Deliberately kept refused - see the report: real .NET two's-
    # complements to the DECLARED bit width of the source integral type
    # (sbyte/short/int/long all give a different string for -1), and
    # pqtools' M numbers carry no such width.
    with pytest.raises(UnsupportedError, match="negative"):
        evaluate('Number.ToText(-1, "X")')


def test_number_to_text_general_format_matches_doc_examples():
    # All three expected strings are copied verbatim from the "General
    # ('G') Format Specifier" section of the doc, including the bare-"G"
    # shortest-round-trip case and the explicit-precision case.
    assert evaluate('Number.ToText(-123.456, "G")') == "-123.456"
    assert evaluate('Number.ToText(123.4546, "G4")') == "123.5"
    assert evaluate('Number.ToText(-1.234567890e-25, "G")') == "-1.23456789E-25"


def test_number_to_text_general_format_zero():
    assert evaluate('Number.ToText(0, "G")') == "0"


def test_number_to_text_nan_and_infinity_ignore_the_format():
    # NaN/Infinity come from NumberFormatInfo.NaNSymbol/*InfinitySymbol* in
    # real .NET, not from the format specifier - every standard format
    # renders them identically to the no-format default.
    assert evaluate('Number.ToText(#nan, "F2")') == "NaN"
    assert evaluate('Number.ToText(#infinity, "F2")') == "Infinity"
    assert evaluate('Number.ToText(#nan, "X")') == "NaN"


def test_number_to_text_unknown_single_letter_format_is_unsupported():
    with pytest.raises(UnsupportedError, match="'Q'"):
        evaluate('Number.ToText(4, "Q")')


def test_number_to_text_null_format_and_null_culture_are_default():
    assert evaluate('Number.ToText(4, null, "en-US")') == "4"
    assert evaluate('Number.ToText(4, "F2", null)') == "4.00"


# --------------------------------------------------------------------------
# Number.ToText - culture argument
# --------------------------------------------------------------------------


def test_number_to_text_honors_en_us_and_invariant_culture():
    assert evaluate('Number.ToText(4, "F2", "en-US")') == "4.00"
    assert evaluate('Number.ToText(4, "F2", "en")') == "4.00"


def test_number_to_text_rejects_other_culture():
    with pytest.raises(UnsupportedError, match="fr-FR"):
        evaluate('Number.ToText(4, "C", "fr-FR")')


def test_number_to_text_rejects_other_culture_naming_which_are_supported():
    with pytest.raises(UnsupportedError, match="invariant/en-US"):
        evaluate('Number.ToText(4, null, "de-DE")')


# --------------------------------------------------------------------------
# Number.ToText - deliberately kept refusal: lowercase standard-format
# letters other than the pre-existing "f" (case controls the exponent/hex-
# digit case in real .NET for E/G/X, a second rendering path this module
# does not implement - see the report). This also happens to be the exact
# pre-existing pinned case in tests/test_builtins_scalar.py, a file this
# task does not own.
# --------------------------------------------------------------------------


def test_number_to_text_lowercase_e_stays_unsupported():
    with pytest.raises(UnsupportedError, match="format"):
        evaluate('Number.ToText(4, "e")')


def test_number_to_text_lowercase_x_and_g_stay_unsupported():
    with pytest.raises(UnsupportedError):
        evaluate('Number.ToText(255, "x")')
    with pytest.raises(UnsupportedError):
        evaluate('Number.ToText(4.5, "g")')


def test_number_to_text_lowercase_c_d_n_p_are_accepted():
    # Real .NET: case is irrelevant to the OUTPUT of these four, so both
    # cases are honoured (matches the pre-existing lowercase-"f" behaviour
    # this module already had).
    assert evaluate('Number.ToText(4, "c")') == "$4.00"
    assert evaluate('Number.ToText(4, "d")') == "4"
    assert evaluate('Number.ToText(1234.5, "n1")') == "1,234.5"
    assert evaluate('Number.ToText(0.5, "p0")') == "50 %"


# --------------------------------------------------------------------------
# Number.ToText - custom picture formats stay refused (out of scope - only
# the .NET STANDARD numeric format strings are implemented).
# --------------------------------------------------------------------------


def test_number_to_text_custom_picture_format_stays_unsupported():
    with pytest.raises(UnsupportedError, match=r"#,##0\.00"):
        evaluate('Number.ToText(1234.5, "#,##0.00")')


# --------------------------------------------------------------------------
# Number.IntegerDivide / Number.Mod - Precision.Double / Precision.Decimal
# --------------------------------------------------------------------------


def test_precision_enum_values():
    # Verified against Power Query's own docs: Precision.Double = 0,
    # Precision.Decimal = 1 (both are just plain numbers in M, exactly
    # like Day.Sunday = 0 already is in _datetime.py).
    assert evaluate("Precision.Double") == 0
    assert evaluate("Precision.Decimal") == 1


def test_number_mod_precision_double_is_the_default_and_shows_the_artifact():
    # 0.3 and 0.1 are not exactly representable in binary - Precision.
    # Double (the default) shows the classic IEEE-754 remainder artifact.
    # Independently derived here from Python's own math.fmod, not hand-
    # typed, so this can never be "the test's number was wrong."
    expected = math.fmod(0.3, 0.1)
    assert expected != 0.0
    assert evaluate("Number.Mod(0.3, 0.1)") == expected
    assert evaluate("Number.Mod(0.3, 0.1, Precision.Double)") == expected
    assert evaluate("Number.Mod(0.3, 0.1, null)") == expected


def test_number_mod_precision_decimal_avoids_the_artifact():
    assert evaluate("Number.Mod(0.3, 0.1, Precision.Decimal)") == 0.0


def test_number_integer_divide_precision_double_is_the_default_and_wrong():
    # Same underlying artifact: 0.3/0.1 as a binary double is very
    # slightly less than 3, so truncating gives 2, not the mathematically
    # correct 3. This is the whole reason Precision.Decimal exists.
    assert evaluate("Number.IntegerDivide(0.3, 0.1)") == 2
    assert evaluate("Number.IntegerDivide(0.3, 0.1, Precision.Double)") == 2


def test_number_integer_divide_precision_decimal_is_correct():
    assert evaluate("Number.IntegerDivide(0.3, 0.1, Precision.Decimal)") == 3


def test_number_integer_divide_precision_decimal_still_truncates_toward_zero():
    # Precision.Decimal changes the ARITHMETIC, not the truncation rule -
    # still toward zero, same as Precision.Double (test_builtins_scalar.py
    # already pins -7/3 = -2 for the double path).
    assert evaluate("Number.IntegerDivide(-7, 3, Precision.Decimal)") == -2


def test_number_mod_precision_decimal_matches_truncated_semantics():
    # Cross-checked against Python's own decimal module directly, not
    # hand-computed.
    d1, d2 = decimal.Decimal("-7"), decimal.Decimal("3")
    quotient = (d1 / d2).to_integral_value(rounding=decimal.ROUND_DOWN)
    expected = float(d1 - quotient * d2)
    assert evaluate("Number.Mod(-7, 3, Precision.Decimal)") == expected
    assert expected == -1.0


def test_precision_null_propagation_still_applies():
    assert evaluate("Number.IntegerDivide(null, 2, Precision.Decimal)") is None
    assert evaluate("Number.Mod(null, 2, Precision.Decimal)") is None


def test_precision_invalid_value_is_eval_error():
    with pytest.raises(EvalError, match="Precision"):
        evaluate("Number.Mod(1, 2, 5)")
    with pytest.raises(EvalError, match="Precision"):
        evaluate("Number.IntegerDivide(1, 2, 5)")


def test_precision_division_by_zero_still_checked_first():
    with pytest.raises(EvalError, match="division by zero"):
        evaluate("Number.IntegerDivide(1, 0, Precision.Decimal)")
    with pytest.raises(EvalError, match="division by zero"):
        evaluate("Number.Mod(1, 0, Precision.Decimal)")


# --------------------------------------------------------------------------
# Date/DateTime/Time.ToText - new custom-pattern tokens: zzz, literal
# quoting ('...'), backslash escaping, and the same-repeated-letter
# tokenizer fix (no separators required between different tokens).
# --------------------------------------------------------------------------


def test_datetime_to_text_zzz_renders_utc_offset():
    assert (
        evaluate('DateTime.ToText(#datetimezone(2009,6,15,13,45,30,-7,0), "zzz")')
        == "-07:00"
    )
    assert (
        evaluate('DateTime.ToText(#datetimezone(2009,6,15,13,45,30,5,30), "zzz")')
        == "+05:30"
    )


def test_datetime_to_text_zzz_on_naive_value_is_eval_error():
    with pytest.raises(EvalError, match="time zone"):
        evaluate('DateTime.ToText(#datetime(2024,1,1,0,0,0), "zzz")')


def test_date_to_text_quoted_literal_passes_through_verbatim():
    assert (
        evaluate("Date.ToText(#date(2024,1,1), \"yyyy'-test-'MM\")") == "2024-test-01"
    )


def test_date_to_text_backslash_escapes_a_single_character():
    # `\M` forces a literal "M" instead of the month token, then "-MM"
    # after it is a real (unescaped) month token again.
    fmt = "yyyy\\M-MM"
    assert evaluate(f'Date.ToText(#date(2024,3,1), "{fmt}")') == "2024M-03"


def test_date_to_text_unterminated_literal_is_eval_error():
    with pytest.raises(EvalError, match="unterminated"):
        evaluate('Date.ToText(#date(2024,1,1), "yyyy\'oops")')


def test_date_to_text_adjacent_tokens_need_no_separator():
    # "yyyyMMdd" must tokenize as three tokens (yyyy, MM, dd), matching
    # real .NET's same-repeated-character tokenizer - NOT one 8-letter
    # blob raising UnsupportedError.
    assert evaluate('Date.ToText(#date(2024,1,1), "yyyyMMdd")') == "20240101"


# --------------------------------------------------------------------------
# Date/DateTime/Time.ToText - standard single-letter formats.
#
# Every expected string below is copied verbatim from the .NET "Standard
# date and time format strings" doc's own worked example
# (2009-06-15T13:45:30, en-US), fetched during this task.
# --------------------------------------------------------------------------

_DT = "#datetime(2009,6,15,13,45,30)"
_DTZ_NEG7 = "#datetimezone(2009,6,15,13,45,30,-7,0)"


@pytest.mark.parametrize(
    ("letter", "expected"),
    [
        ("d", "6/15/2009"),
        ("D", "Monday, June 15, 2009"),
        ("f", "Monday, June 15, 2009 1:45 PM"),
        ("F", "Monday, June 15, 2009 1:45:30 PM"),
        ("g", "6/15/2009 1:45 PM"),
        ("G", "6/15/2009 1:45:30 PM"),
        ("m", "June 15"),
        ("M", "June 15"),
        ("t", "1:45 PM"),
        ("T", "1:45:30 PM"),
        ("y", "June 2009"),
        ("Y", "June 2009"),
        ("s", "2009-06-15T13:45:30"),
    ],
)
def test_datetime_to_text_standard_single_letter_formats(letter, expected):
    assert evaluate(f'DateTime.ToText({_DT}, "{letter}")') == expected


def test_datetime_to_text_round_trip_format():
    # Doc pattern: yyyy'-'MM'-'dd'T'HH':'mm':'ss'.'fffffffK. A naive value
    # has an empty "K" (no time zone), and the 7th fractional digit is
    # always 0 - this module's datetime never carries sub-microsecond
    # precision, so that is a deterministic zero-pad, not a guess.
    assert evaluate(f'DateTime.ToText({_DT}, "o")') == "2009-06-15T13:45:30.0000000"
    assert evaluate(f'DateTime.ToText({_DT}, "O")') == "2009-06-15T13:45:30.0000000"


def test_datetime_to_text_round_trip_format_with_zone():
    assert (
        evaluate(f'DateTime.ToText({_DTZ_NEG7}, "o")')
        == "2009-06-15T13:45:30.0000000-07:00"
    )


def test_datetime_to_text_rfc1123_format_naive_no_conversion():
    # Doc example: DateTime input 2009-06-15T13:45:30 -> "Mon, 15 Jun 2009
    # 13:45:30 GMT" (no numeric shift for a naive/local value).
    assert evaluate(f'DateTime.ToText({_DT}, "r")') == "Mon, 15 Jun 2009 13:45:30 GMT"


def test_datetime_to_text_rfc1123_format_aware_converts_to_utc():
    # Doc example: DateTimeOffset input 2009-06-15T13:45:30 (at -7) ->
    # "Mon, 15 Jun 2009 20:45:30 GMT" (converted to UTC first).
    assert (
        evaluate(f'DateTime.ToText({_DTZ_NEG7}, "r")')
        == "Mon, 15 Jun 2009 20:45:30 GMT"
    )


def test_datetime_to_text_universal_sortable_format():
    # Doc example: DateTimeOffset input -> "2009-06-15 20:45:30Z".
    assert evaluate(f'DateTime.ToText({_DTZ_NEG7}, "u")') == "2009-06-15 20:45:30Z"
    # Naive input: no conversion, just the "Z" label (doc's DateTime row).
    assert evaluate(f'DateTime.ToText({_DT}, "u")') == "2009-06-15 13:45:30Z"


def test_datetime_to_text_universal_full_format_requires_a_time_zone():
    # Doc example (aware, converted to UTC): "Monday, June 15, 2009 8:45:30
    # PM (en-US)".
    assert (
        evaluate(f'DateTime.ToText({_DTZ_NEG7}, "U")')
        == "Monday, June 15, 2009 8:45:30 PM"
    )
    # Deliberately kept refused for a naive value: real .NET falls back to
    # the MACHINE's local time zone here, which pqtools never depends on
    # (see the report).
    with pytest.raises(UnsupportedError, match="local time zone"):
        evaluate(f'DateTime.ToText({_DT}, "U")')


def test_date_to_text_standard_single_letter_format():
    assert evaluate('Date.ToText(#date(2009,6,15), "d")') == "6/15/2009"
    assert evaluate('Date.ToText(#date(2009,6,15), "D")') == "Monday, June 15, 2009"


def test_time_to_text_standard_single_letter_format():
    assert evaluate('Time.ToText(#time(13,45,30), "t")') == "1:45 PM"
    assert evaluate('Time.ToText(#time(13,45,30), "T")') == "1:45:30 PM"


def test_date_to_text_time_only_standard_format_is_unsupported():
    with pytest.raises(UnsupportedError, match="time component"):
        evaluate('Date.ToText(#date(2009,6,15), "t")')


def test_time_to_text_date_only_standard_format_is_unsupported():
    with pytest.raises(UnsupportedError, match="date component"):
        evaluate('Time.ToText(#time(13,45,30), "d")')


def test_datetime_to_text_unrecognised_single_letter_is_unsupported():
    with pytest.raises(UnsupportedError, match="'Q'"):
        evaluate(f'DateTime.ToText({_DT}, "Q")')


def test_date_to_text_unrecognised_single_letter_names_it_not_the_shape_mismatch():
    # "Q" is not a real standard-format letter at all, so the message must
    # not falsely claim it "requires a time component."
    with pytest.raises(UnsupportedError) as excinfo:
        evaluate('Date.ToText(#date(2009,6,15), "Q")')
    assert "time component" not in str(excinfo.value)


# --------------------------------------------------------------------------
# Deliberately kept refusals outside this task's scope (named here so a
# reader does not mistake the omission for an oversight).
# --------------------------------------------------------------------------


def test_duration_to_text_custom_format_stays_unsupported():
    # Duration.ToText's picture-format argument is a different feature
    # (duration format strings, e.g. "d.hh:mm:ss") not named in this
    # task's "What to implement" section - kept refused rather than
    # guessed at.
    with pytest.raises(UnsupportedError):
        evaluate('Duration.ToText(#duration(1,2,3,4), "d.hh:mm:ss")')


def test_date_to_text_unknown_option_record_key_stays_unsupported():
    # Input validation for a garbage options-record key, unrelated to
    # culture/format-string support - unchanged by this task.
    with pytest.raises(UnsupportedError):
        evaluate('Date.ToText(#date(2024,1,1), [Bogus="x"])')
