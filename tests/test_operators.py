"""The operator layer, against Microsoft's own operand tables.

Every assertion below is either an example printed in the M specification
(learn.microsoft.com/en-us/powerquery-m/m-spec-operators) or a row of one of
its four arithmetic tables, the relational operand rule, or the `&` table.

Until 0.10.0 this evaluator ran ``_require_number`` on both sides of every
arithmetic operator, so the language outside numbers was absent. That is not
a corner-case gap. It meant:

    [Amount] + [Fee]                        raised on any blank cell
    DateTime.LocalNow() + #duration(0,1,0,0)  could not run at all
    [Total] / [Count]                       raised on a zero count

The first is the single most common shape in a spreadsheet-derived query,
the second is how the reference writes its own temporal examples, and the
third returns #infinity in Power Query. All three ran there and failed here,
which is the wrong direction for a tool whose whole promise is that a query
behaves the same in both places.

A thousand green tests did not notice, because the fixtures were written by
people who reach for numbers when they need "a value".
"""

from __future__ import annotations

import datetime
import math

import pytest

from pqtools import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# null propagates - the row every arithmetic table repeats
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "1 + null",
        "null + 1",
        "1 - null",
        "null - 1",
        "6 * null",
        "null * 6",
        "0 / null",
        "null / 6",
        "#duration(0,1,0,0) + null",
        "#date(2024,1,1) - null",
    ],
)
def test_an_operand_of_null_yields_null(source: str) -> None:
    """`6 * null // null` is the spec's own example.

    This is the failure that mattered most in practice. A blank cell in
    Power Query is `null`, so `[Amount] + [Fee]` over a column with one gap
    used to raise "expected a number, got null" - on data Power Query adds
    up without complaint.
    """
    assert evaluate(source) is None


def test_null_on_both_sides_is_also_null() -> None:
    """Not enumerated in the arithmetic tables; chosen, and here is why.

    The relational section states the rule in general terms - "if either or
    both operands are null, the result is the null value" - and two blank
    cells added together is an ordinary thing for a query to do. Erroring
    would reintroduce the failure the row above exists to fix, one column
    later.
    """
    assert evaluate("null + null") is None


# --------------------------------------------------------------------------
# Division follows IEEE 754. It does not raise.
# --------------------------------------------------------------------------


def test_the_specs_own_division_examples() -> None:
    assert evaluate("8 / 2") == 4
    assert evaluate("8 / 0") == math.inf
    assert math.isnan(evaluate("0 / 0"))
    assert evaluate("0 / null") is None
    assert math.isnan(evaluate("#nan / #infinity"))


def test_the_sign_of_a_zero_denominator_is_read() -> None:
    """+x / -0 is -∞ in the spec's table.

    `right < 0` cannot see a negative zero; `math.copysign` can. Getting it
    wrong flips the sign of an infinity rather than erroring, which is the
    kind of wrong that survives a review.
    """
    assert evaluate("8 / -0.0") == -math.inf
    assert evaluate("-8 / -0.0") == math.inf


def test_integer_division_and_modulo_still_refuse_a_zero_divisor() -> None:
    # These are not the `/` operator and have no IEEE result to hand back.
    for source in ("Number.IntegerDivide(1, 0)", "Number.Mod(1, 0)"):
        with pytest.raises(EvalError, match="division by zero"):
            evaluate(source)


# --------------------------------------------------------------------------
# Durations and the datetime family
# --------------------------------------------------------------------------


def test_sum_of_durations_is_the_specs_example() -> None:
    # #duration(2,1,0,15.1) + #duration(0,1,30,45.3) // #duration(2,2,31,0.4)
    assert evaluate("#duration(2,1,0,15.1) + #duration(0,1,30,45.3)") == (
        datetime.timedelta(days=2, hours=2, minutes=31, seconds=0.4)
    )


def test_quotient_of_durations_is_the_specs_example() -> None:
    # #duration(2,0,0,0) / #duration(0,1,30,0) // 32
    assert evaluate("#duration(2,0,0,0) / #duration(0,1,30,0)") == 32


def test_scaled_duration_is_the_specs_example() -> None:
    # #duration(2,0,0,0) / 32 // #duration(0,1,30,0)
    assert evaluate("#duration(2,0,0,0) / 32") == datetime.timedelta(
        hours=1, minutes=30
    )
    assert evaluate("#duration(0,1,0,0) * 3") == datetime.timedelta(hours=3)
    assert evaluate("3 * #duration(0,1,0,0)") == datetime.timedelta(hours=3)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#date(2024,1,1) + #duration(1,0,0,0)", datetime.date(2024, 1, 2)),
        ("#duration(1,0,0,0) + #date(2024,1,1)", datetime.date(2024, 1, 2)),
        ("#date(2024,1,2) - #duration(1,0,0,0)", datetime.date(2024, 1, 1)),
        (
            "#datetime(2024,1,1,9,0,0) + #duration(0,2,30,0)",
            datetime.datetime(2024, 1, 1, 11, 30),
        ),
        ("#time(9,0,0) + #duration(0,2,0,0)", datetime.time(11, 0)),
        ("#time(9,0,0) - #duration(0,2,0,0)", datetime.time(7, 0)),
    ],
)
def test_a_datetime_offset_by_a_duration_keeps_its_own_type(
    source: str, expected: object
) -> None:
    """ "the resulting value is of that same type" - a date stays a date."""
    result = evaluate(source)
    assert result == expected
    assert type(result) is type(expected)


def test_an_aware_datetime_keeps_its_zone() -> None:
    result = evaluate("#datetimezone(2024,1,1,12,0,0,5,30) + #duration(0,1,0,0)")
    assert result.hour == 13
    assert result.utcoffset() == datetime.timedelta(hours=5, minutes=30)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#date(2024,1,2) - #date(2024,1,1)", datetime.timedelta(days=1)),
        (
            "#datetime(2024,1,1,12,0,0) - #datetime(2024,1,1,9,30,0)",
            datetime.timedelta(hours=2, minutes=30),
        ),
        ("#time(12,0,0) - #time(9,30,0)", datetime.timedelta(hours=2, minutes=30)),
    ],
)
def test_the_difference_of_two_datetimes_is_a_duration(
    source: str, expected: datetime.timedelta
) -> None:
    assert evaluate(source) == expected


def test_two_aware_datetimes_subtract_across_their_offsets() -> None:
    """The spec normalises to UTC before comparing; so must subtraction.

    Same wall clock, different zones, so the answer is the offset gap and
    not zero.
    """
    assert evaluate(
        "#datetimezone(2024,1,1,12,0,0,0,0) - #datetimezone(2024,1,1,12,0,0,5,30)"
    ) == datetime.timedelta(hours=5, minutes=30)


def test_time_arithmetic_that_leaves_the_day_is_refused() -> None:
    """M's `time` has no date to carry into, so 23:00 + 2h has no answer.

    Wrapping to 01:00 would be a silent invention: the caller asked for a
    point two hours later, and 01:00 is twenty-two hours earlier.
    """
    with pytest.raises(EvalError, match="left the day"):
        evaluate("#time(23,0,0) + #duration(0,2,0,0)")


def test_a_duration_minus_a_datetime_has_no_row_and_no_meaning() -> None:
    with pytest.raises(EvalError, match="operator - is not defined"):
        evaluate("#duration(0,1,0,0) - #date(2024,1,1)")


def test_a_number_divided_by_a_duration_has_no_row() -> None:
    with pytest.raises(EvalError, match="operator / is not defined"):
        evaluate("1 / #duration(0,1,0,0)")


def test_the_error_names_m_types_not_python_ones() -> None:
    """A reader greps their query for the type the message names.

    "timedelta" is not a type Power Query has, so a message using it sends
    them looking for something that was never in their query.
    """
    with pytest.raises(EvalError, match="duration and date"):
        evaluate("#duration(0,1,0,0) - #date(2024,1,1)")


# --------------------------------------------------------------------------
# The relational operators
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source", ["null < 1", "1 < null", "null <= null", "null >= #date(2024,1,1)"]
)
def test_a_null_operand_makes_the_comparison_null(source: str) -> None:
    """ "If either or both operands are null, the result is the null value."

    `null <= null // null` is the spec's own example. Not false, and not an
    error: `[Ship Date] < #date(...)` over a blank cell is a null the
    surrounding filter then treats as not-true, which is how Power Query
    drops those rows instead of failing the refresh.
    """
    assert evaluate(source) is None


def test_two_binaries_are_compared_byte_by_byte() -> None:
    """The spec says so in those words, and binary is on the operand list."""
    assert evaluate("#binary({1,2}) < #binary({1,3})") is True
    assert evaluate("#binary({1,2}) > #binary({1,3})") is False
    assert evaluate("#binary({1}) < #binary({1,0})") is True
    assert evaluate("#binary({1,2}) <= #binary({1,2})") is True


def test_true_is_greater_than_false() -> None:
    """ "Two logicals are compared such that true is considered to be greater
    than false" - and logical is on the operand list, so this is ordering,
    not a type error."""
    assert evaluate("true > false") is True
    assert evaluate("false > true") is False
    assert evaluate("true >= true") is True


def test_durations_are_ordered() -> None:
    assert evaluate("#duration(0,1,0,0) < #duration(0,2,0,0)") is True


def test_nan_is_false_for_every_relational_operator() -> None:
    # "If either operand is #nan, the result is false for all relational
    # operators." Including #nan against itself.
    for operator in ("<", "<=", ">", ">="):
        assert evaluate(f"#nan {operator} #nan") is False
        assert evaluate(f"#nan {operator} 1") is False


def test_mismatched_kinds_are_still_a_type_error() -> None:
    # Widening the accepted kinds must not have made everything comparable.
    for source in ('1 < "a"', "#date(2024,1,1) < 5", "true < 1", '#binary({1}) < "a"'):
        with pytest.raises(EvalError, match="relational operators require"):
            evaluate(source)


def test_equality_stays_total_where_ordering_is_not() -> None:
    # `=` answers for any pair; `<` refuses. Both were already true and both
    # must stay true - a null-propagating `<` must not leak into `=`.
    assert evaluate('1 = "1"') is False
    assert evaluate("null = null") is True
    assert evaluate("null = true") is False
    assert evaluate("#binary({1,2}) = #binary({1,2})") is True
    assert evaluate("#binary({1,2}) <> #binary({1,3})") is True


# --------------------------------------------------------------------------
# The combination operator
# --------------------------------------------------------------------------


def test_the_specs_own_combination_examples() -> None:
    assert evaluate('"AB" & "CDE"') == "ABCDE"
    assert evaluate("{1, 2} & {3}") == [1, 2, 3]
    assert evaluate("[x = 1] & [y = 2]") == {"x": 1, "y": 2}
    # "If a field appears in both x and y, the value from y is used", and the
    # order is x's fields then y's new ones.
    assert list(evaluate("[x = 1, y = 2] & [x = 3, z = 4]").items()) == [
        ("x", 3),
        ("y", 2),
        ("z", 4),
    ]
    # #date(2013,02,26) & #time(09,17,00) // #datetime(2013,02,26,09,17,00)
    assert evaluate("#date(2013,02,26) & #time(09,17,00)") == datetime.datetime(
        2013, 2, 26, 9, 17
    )


@pytest.mark.parametrize(
    "source",
    ['"a" & null', 'null & "a"', "#date(2024,1,1) & null", "null & #time(1,0,0)"],
)
def test_a_null_operand_of_ampersand_yields_null(source: str) -> None:
    assert evaluate(source) is None


def test_ampersand_is_not_defined_for_a_number() -> None:
    with pytest.raises(EvalError, match="operator & is not defined"):
        evaluate('1 & "a"')
    with pytest.raises(EvalError, match="operator & is not defined"):
        evaluate("null & 1")


def test_two_lists_of_records_with_different_fields_are_refused() -> None:
    """The one place `&` cannot be answered in this data model.

    A table here IS a list of records, and M concatenates two lists item by
    item but unions the COLUMNS of two tables, filling gaps with null. The
    two readings give different data, so picking one silently returns a
    ragged list where a table was meant, or invents null cells where a plain
    list was meant. The error names the function for each meaning.
    """
    with pytest.raises(UnsupportedError, match="Table.Combine"):
        evaluate("{[a=1]} & {[b=2]}")


def test_matching_fields_are_not_ambiguous_and_still_concatenate() -> None:
    # Both readings agree here, so refusing would be pure obstruction.
    assert evaluate("{[a=1]} & {[a=2]}") == [{"a": 1}, {"a": 2}]
    assert evaluate("{1, 2} & {[a=1]}") == [1, 2, {"a": 1}]


# --------------------------------------------------------------------------
# Unary operators
# --------------------------------------------------------------------------


def test_the_specs_own_unary_examples() -> None:
    assert evaluate("+ - 1") == -1
    assert evaluate("+ + 1") == 1
    assert math.isnan(evaluate("+ #nan"))
    assert evaluate("- (1 + 1)") == -2
    assert evaluate("- - 1") == 1
    assert evaluate("- #infinity") == -math.inf
    # + #duration(0,1,30,0) // #duration(0,1,30,0)
    assert evaluate("+ #duration(0,1,30,0)") == datetime.timedelta(hours=1, minutes=30)
    # - #duration(0,1,30,0) // #duration(0,-1,-30,0)
    assert evaluate("- #duration(0,1,30,0)") == -datetime.timedelta(hours=1, minutes=30)


def test_unary_operators_pass_null_through() -> None:
    assert evaluate("- null") is None
    assert evaluate("+ null") is None


def test_unary_minus_is_not_defined_for_text() -> None:
    with pytest.raises(EvalError, match="unary - is not defined for text"):
        evaluate('- "x"')
