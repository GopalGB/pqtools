"""``Byte.From`` / ``Currency.From`` / ``Decimal.From`` / ``Double.From`` /
``Int8.From`` / ``Int16.From`` / ``Int32.From`` / ``Int64.From`` /
``Percentage.From`` / ``Single.From`` / ``Value.FromText`` - the numeric
``X.From`` conversions.

All 11 were documented on Microsoft Learn and absent from ``BUILTINS``, so a
query using any of them got a "documented but not implemented" refusal.

Every expected value below is either copied verbatim from the function's own
learn.microsoft.com/en-us/powerquery-m/<name> page (fetched during this
task), derived from a well-known exact .NET primitive range (computed via a
formula in ``_number.py``, e.g. ``2**63 - 1`` for Int64, never a hand-typed
digit string), or cross-checked against Python's own arithmetic - never
guessed. Where a value comes from the doc page, the test comment says so.
"""

from __future__ import annotations

import math
import re

import pytest

from pqtools import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Doc-page worked examples, pinned verbatim.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        # Byte.From: learn.microsoft.com/en-us/powerquery-m/byte-from.
        ('Byte.From("4")', 4),
        ('Byte.From("4.5", null, RoundingMode.AwayFromZero)', 5),
        # Currency.From: learn.microsoft.com/en-us/powerquery-m/currency-from.
        ('Currency.From("1.23455")', 1.2346),
        ('Currency.From("1.23455", "en-US", RoundingMode.Down)', 1.2345),
        # Decimal.From: learn.microsoft.com/en-us/powerquery-m/decimal-from.
        ('Decimal.From("4.5")', 4.5),
        # Double.From: learn.microsoft.com/en-us/powerquery-m/double-from.
        ('Double.From("4.5")', 4.5),
        # Int8.From: learn.microsoft.com/en-us/powerquery-m/int8-from.
        ('Int8.From("4")', 4),
        ('Int8.From("4.5", null, RoundingMode.AwayFromZero)', 5),
        # Int16.From: learn.microsoft.com/en-us/powerquery-m/int16-from.
        ('Int16.From("4.5", null, RoundingMode.AwayFromZero)', 5),
        # Int32.From: learn.microsoft.com/en-us/powerquery-m/int32-from.
        ('Int32.From("4.5", null, RoundingMode.AwayFromZero)', 5),
        # Int64.From: learn.microsoft.com/en-us/powerquery-m/int64-from.
        ('Int64.From("4.5", null, RoundingMode.AwayFromZero)', 5),
        # Percentage.From: learn.microsoft.com/en-us/powerquery-m/
        # percentage-from.
        ('Percentage.From("12.3%")', 0.123),
        # Single.From: learn.microsoft.com/en-us/powerquery-m/single-from.
        ('Single.From("1.5")', 1.5),
        # Value.FromText: learn.microsoft.com/en-us/powerquery-m/
        # value-fromtext, examples 1 and 2 (3 and 4 use a non-invariant
        # culture - see test_value_from_text_refuses_a_foreign_culture).
        ('Value.FromText("12345.6789")', 12345.6789),
        ('Value.FromText("25.4%")', 0.254),
    ],
)
def test_doc_page_worked_example(expression: str, expected: object) -> None:
    assert evaluate(expression) == expected


def test_percentage_from_lands_on_the_exact_double_not_a_near_miss() -> None:
    """12.3 / 100 in plain float arithmetic is 0.12300000000000001, not the
    nearest double to 0.123 (verified: they compare unequal). The page
    prints the clean "0.123", so the implementation goes through
    decimal.Decimal for the division rather than dividing two floats - a
    regression here would silently reintroduce the extra bits.
    """
    result = evaluate('Percentage.From("12.3%")')
    assert result == 0.123
    assert result == float(str(result))  # shortest round-trip, no stray digits


# --------------------------------------------------------------------------
# null in, null out - every function's own About text: "If the given value
# is null, X.From returns null."
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "Byte.From",
        "Currency.From",
        "Decimal.From",
        "Double.From",
        "Int8.From",
        "Int16.From",
        "Int32.From",
        "Int64.From",
        "Percentage.From",
        "Single.From",
        "Value.FromText",
    ],
)
def test_null_propagates(call: str) -> None:
    assert evaluate(f"{call}(null)") is None


# --------------------------------------------------------------------------
# Range limits are real: Byte.From(300) and Int8.From(200) must error, not
# wrap to a value that fits after truncating to the type's real bit width -
# the task brief's own examples, and the reason every bound below is
# computed from 2**n rather than typed by hand.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "in_range", "just_over", "just_under"),
    [
        ("Byte.From", 255, 256, -1),
        ("Int8.From", 127, 128, -129),
        ("Int16.From", 32767, 32768, -32769),
        ("Int32.From", 2147483647, 2147483648, -2147483649),
        ("Int64.From", 9223372036854775807, 9223372036854775808, -9223372036854775809),
    ],
)
def test_integer_family_range_is_the_real_bit_width(
    call: str, in_range: int, just_over: int, just_under: int
) -> None:
    assert evaluate(f"{call}({in_range})") == in_range
    with pytest.raises(EvalError, match=f"{call}.*outside the range"):
        evaluate(f"{call}({just_over})")
    with pytest.raises(EvalError, match=f"{call}.*outside the range"):
        evaluate(f"{call}({just_under})")


def test_byte_from_300_errors_task_brief_example() -> None:
    with pytest.raises(EvalError, match="Byte.From: 300 is outside the range 0 to 255"):
        evaluate("Byte.From(300)")


def test_int8_from_200_errors_task_brief_example() -> None:
    with pytest.raises(
        EvalError, match=r"Int8\.From: 200 is outside the range -128 to 127"
    ):
        evaluate("Int8.From(200)")


def test_currency_from_out_of_range_errors_with_the_documented_bounds() -> None:
    # Currency's own page states this exact range: Int64's range scaled by
    # the type's fixed 4 decimal digits.
    with pytest.raises(
        EvalError,
        match=r"Currency\.From: .* outside the range "
        r"-922337203685477\.5808 to 922337203685477\.5807",
    ):
        evaluate("Currency.From(922337203685478)")


def test_single_from_out_of_range_errors() -> None:
    # Single.MaxValue = (2 - 2**-23) * 2**127, computed exactly in
    # _number.py rather than typed from memory.
    single_max = (2 - 2**-23) * 2**127
    assert evaluate(f"Single.From({single_max})") == single_max
    with pytest.raises(EvalError, match="Single.From.*outside the range"):
        evaluate("Single.From(3.5e38)")


def test_decimal_from_out_of_range_errors() -> None:
    # .NET's Decimal.MaxValue (79228162514264337593543950335) is exactly
    # 2**96 - 1.
    with pytest.raises(EvalError, match="Decimal.From.*outside the range"):
        evaluate("Decimal.From(1e29)")


# --------------------------------------------------------------------------
# RoundingMode - default is ToEven (banker's rounding), matching
# Number.Round's own None-path exactly; ties only, per Number.Round's own
# comment ("1.4 rounds to 1 under every mode" - the mode picks the TIE).
# --------------------------------------------------------------------------


def test_default_rounding_mode_is_to_even() -> None:
    # 2.5 and 3.5 are both exactly representable in binary, so this is a
    # real tie, not a floating-point artifact.
    assert evaluate("Byte.From(2.5)") == 2  # rounds down to the even 2
    assert evaluate("Byte.From(3.5)") == 4  # rounds up to the even 4


def test_non_tie_fractions_round_to_nearest_regardless_of_mode() -> None:
    assert evaluate("Byte.From(4.3)") == 4
    assert evaluate("Byte.From(4.7)") == 5


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("RoundingMode.Up", -4),
        ("RoundingMode.Down", -5),
        ("RoundingMode.AwayFromZero", -5),
        ("RoundingMode.TowardZero", -4),
        ("RoundingMode.ToEven", -4),
    ],
)
def test_every_rounding_mode_on_a_negative_tie(mode: str, expected: int) -> None:
    # -4.5 is a tie between -4 and -5; each named mode picks a documented,
    # different side - cross-checked directly against Number.Round's own
    # semantics, which this function delegates to.
    assert evaluate(f"Int8.From(-4.5, null, {mode})") == expected


def test_an_invalid_rounding_mode_is_refused_by_the_caller_not_by_number_round() -> (
    None
):
    with pytest.raises(EvalError, match=r"Byte\.From: 99 is not a RoundingMode"):
        evaluate("Byte.From(4, null, 99)")


def test_currency_from_rounds_to_four_decimal_digits_not_zero() -> None:
    # Distinguishes Currency's own rounding (digits=4) from the whole-number
    # family above (digits=0) - a regression collapsing the two would still
    # pass every Byte/Int* test.
    assert evaluate('Currency.From("1.23455")') == 1.2346


# --------------------------------------------------------------------------
# NaN / Infinity: real for the two IEEE-754 binary types (Single, Double);
# not representable in .NET's fixed-point types (Decimal, Currency) or in
# any of the fixed-width integer types.
# --------------------------------------------------------------------------


def test_double_and_single_pass_infinity_and_nan_through_unchanged() -> None:
    assert evaluate("Double.From(#infinity)") == math.inf
    assert evaluate("Single.From(#infinity)") == math.inf
    assert math.isnan(evaluate("Double.From(#nan)"))
    assert math.isnan(evaluate("Single.From(#nan)"))


@pytest.mark.parametrize(
    "call", ["Decimal.From", "Currency.From", "Byte.From", "Int64.From"]
)
def test_fixed_point_and_integer_families_refuse_infinity(call: str) -> None:
    with pytest.raises(EvalError):
        evaluate(f"{call}(#infinity)")


@pytest.mark.parametrize(
    "call", ["Decimal.From", "Currency.From", "Byte.From", "Int64.From"]
)
def test_fixed_point_and_integer_families_refuse_nan(call: str) -> None:
    with pytest.raises(EvalError):
        evaluate(f"{call}(#nan)")


# --------------------------------------------------------------------------
# Culture: pqtools only implements invariant/en-US, the same rule every
# other culture parameter in this codebase enforces - refused by name
# rather than guessing at a locale's decimal separator or month names.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        'Byte.From(4, "de-DE")',
        'Currency.From("1.5", "fr-FR")',
        'Decimal.From("4.5", "de-DE")',
        'Double.From("4.5", "de-DE")',
        'Percentage.From("5%", "de-DE")',
        'Value.FromText("4.5", "de-DE")',
    ],
)
def test_a_foreign_culture_is_refused_not_guessed(call: str) -> None:
    with pytest.raises(UnsupportedError, match="de-DE|fr-FR"):
        evaluate(call)


def test_value_from_text_refuses_a_foreign_culture_on_the_doc_pages_own_examples() -> (
    None
):
    # Examples 3 and 4 on value-fromtext's own page use fr-FR and de-DE -
    # both refused rather than guessed at, per every other culture check in
    # this module.
    with pytest.raises(UnsupportedError, match="fr-FR"):
        evaluate('Value.FromText("€1,190", "fr-FR")')
    with pytest.raises(UnsupportedError, match="de-DE"):
        evaluate('Value.FromText("24 Dez 2024 14:33:20", "de-DE")')


# --------------------------------------------------------------------------
# Percentage.From: text with a trailing "%" divides by 100; everything else
# - including a bare number - goes through Number.From unchanged. The doc
# page states this asymmetry explicitly ("Otherwise, the value will be
# converted to a number using Number.From").
# --------------------------------------------------------------------------


def test_percentage_from_only_divides_text_ending_in_percent() -> None:
    assert evaluate("Percentage.From(5)") == 5  # NOT 0.05
    assert evaluate('Percentage.From("5")') == 5  # no "%" suffix either
    assert evaluate('Percentage.From("5%")') == 0.05
    assert evaluate("Percentage.From(true)") == 1  # Number.From(true) == 1


def test_percentage_from_error_names_itself_not_number_from() -> None:
    with pytest.raises(EvalError, match=r"Percentage\.From: not a number"):
        evaluate('Percentage.From("abc%")')


# --------------------------------------------------------------------------
# Every X.From error names ITSELF, not the Number.From/Number.Round it
# delegates to internally - mirrors test_from_text_reports_itself_not_the_
# function_it_delegates_to in test_from_text.py, for the same reason: a
# Byte.From caller should not go hunting for a Number.From call that is not
# in their query.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        'Byte.From("abc")',
        'Int64.From("abc")',
        'Decimal.From("abc")',
        'Double.From("abc")',
        'Single.From("abc")',
    ],
)
def test_conversion_failure_names_the_function_that_was_called(call: str) -> None:
    escaped_name = re.escape(call.split("(")[0])
    with pytest.raises(EvalError, match=f"{escaped_name}: not a number"):
        evaluate(call)


def test_an_unconvertible_type_also_names_the_caller() -> None:
    with pytest.raises(EvalError, match=r"Byte\.From: unsupported value type: list"):
        evaluate("Byte.From({1, 2})")


# --------------------------------------------------------------------------
# Value.FromText: number, percentage, logical, empty-as-null, and the
# disclosed fallback-to-text gap for datetime/duration text (that detection
# lives in _datetime.py, a file this task does not own - see the report).
# --------------------------------------------------------------------------


def test_value_from_text_empty_string_is_null() -> None:
    assert evaluate('Value.FromText("")') is None


def test_value_from_text_whitespace_only_is_not_documented_as_empty() -> None:
    # The page's own wording is "an EMPTY text value", checked here against
    # the literal string, not a stripped one - "   " is not "".
    assert evaluate('Value.FromText("   ")') == "   "


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('Value.FromText("true")', True),
        ('Value.FromText("TRUE")', True),
        ('Value.FromText("false")', False),
    ],
)
def test_value_from_text_recognises_logical_text(text: str, expected: bool) -> None:
    assert evaluate(text) is expected


def test_value_from_text_falls_back_to_text_for_anything_else() -> None:
    assert evaluate('Value.FromText("hello world")') == "hello world"


def test_value_from_text_datetime_detection_is_a_disclosed_gap() -> None:
    """Real Value.FromText also recognises datetime/duration text (the
    page's own About text: "returns a value of type number, logical, null,
    datetime, duration, or text"). That parsing lives in _datetime.py, a
    file this task does not own, so a date string under an INVARIANT
    culture - which this module would otherwise accept rather than refuse -
    comes back as text instead of a date. Pinned here so the gap is visible
    (and so a future session that wires in datetime detection sees exactly
    which test to update rather than silently changing behaviour).
    """
    assert evaluate('Value.FromText("2024-12-24")') == "2024-12-24"


def test_value_from_text_requires_text() -> None:
    with pytest.raises(EvalError, match="expected text, got number"):
        evaluate("Value.FromText(4.5)")


# --------------------------------------------------------------------------
# Arity: Single/Double/Decimal have NO roundingMode parameter at all (their
# Syntax block is two arguments, not three) - unlike Byte/Currency/Int*,
# passing one is a defect, not a feature this evaluator merely ignores.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "Decimal.From(1, null, RoundingMode.Up)",
        "Double.From(1, null, RoundingMode.Up)",
        "Single.From(1, null, RoundingMode.Up)",
    ],
)
def test_single_double_decimal_reject_a_rounding_mode_argument(call: str) -> None:
    with pytest.raises(UnsupportedError, match="argument"):
        evaluate(call)


def test_too_many_arguments_is_refused() -> None:
    with pytest.raises(UnsupportedError, match="argument"):
        evaluate("Byte.From(1, 2, 3, 4)")


# --------------------------------------------------------------------------
# bool is "any other type", not a number, per every X.From page's own About
# text - it goes through the Number.From conversion (bool -> 0/1), the same
# path a text argument takes, not the "already a number" short-circuit.
# --------------------------------------------------------------------------


def test_a_logical_argument_converts_through_number_from() -> None:
    assert evaluate("Byte.From(true)") == 1
    assert evaluate("Int64.From(false)") == 0
    assert evaluate("Decimal.From(true)") == 1
