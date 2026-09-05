"""Optional arguments the List.* functions are documented to take.

Every one of these was missing, and every one is used by a worked example on
Microsoft's own reference page - so the gap was not theoretical: a query
copied verbatim out of the documentation failed on arity here.

The pattern is the one this repo keeps re-finding. A fixture author writing
`List.Sort({3,1,2})` never writes the second argument, so a suite written by
the library's own authors cannot see that the second argument is missing.
Only running someone else's examples finds it.
"""

from __future__ import annotations

import datetime

import pytest

from pqtools import UnsupportedError, evaluate

# --------------------------------------------------------------------------
# List.Sort - the four documented comparisonCriteria shapes
# --------------------------------------------------------------------------


def test_sort_takes_an_order_value() -> None:
    """`List.Sort(list, Order.Descending)` is the page's Example 2.

    Without it, the single most common way to sort a list descending raises
    an arity error.
    """
    assert evaluate("List.Sort({2, 3, 1}, Order.Descending)") == [3, 2, 1]
    assert evaluate("List.Sort({2, 3, 1}, Order.Ascending)") == [1, 2, 3]


def test_sort_takes_a_key_selector() -> None:
    assert evaluate('List.Sort({"ccc", "a", "bb"}, each Text.Length(_))') == [
        "a",
        "bb",
        "ccc",
    ]


def test_sort_takes_a_key_and_an_order() -> None:
    """The `{each 1 / _, Order.Descending}` shape the About text spells out."""
    assert evaluate("List.Sort({2, 3, 1}, {each 1 / _, Order.Descending})") == [
        1,
        2,
        3,
    ]


def test_sort_takes_a_two_argument_comparer() -> None:
    """Example 3, verbatim. A comparer returns -1/0/1, not a logical."""
    assert evaluate("List.Sort({2, 3, 1}, (x, y) => Value.Compare(1/x, 1/y))") == [
        3,
        2,
        1,
    ]


def test_sort_puts_null_first_instead_of_refusing_to_sort() -> None:
    """One blank cell used to turn a sort into "values are not comparable".

    Python will not compare None with an int, so `List.Sort({3, null, 1})`
    raised. M orders null below every other value - the same rule
    Value.Compare already implements here - so it sorts fine, and a column
    with a blank in it is the ordinary case, not the exotic one.
    """
    assert evaluate("List.Sort({3, null, 1})") == [None, 1, 3]
    assert evaluate("List.Sort({3, null, 1}, Order.Descending)") == [3, 1, None]


# --------------------------------------------------------------------------
# List.Distinct / List.Contains - equationCriteria
# --------------------------------------------------------------------------


def test_distinct_takes_a_key_selector() -> None:
    """Example 2: unique by text length, first occurrence wins."""
    source = '{"Apple", "Banana", "Cherry", "Date", "Fig"}'
    assert evaluate(f"List.Distinct(List.Reverse({source}), each Text.Length(_))") == [
        "Fig",
        "Date",
        "Cherry",
        "Apple",
    ]


def test_distinct_takes_a_comparer() -> None:
    """Example 3: case-insensitive dedupe keeps the first spelling seen."""
    source = '{"apple", "Pear", "aPPle", "banana", "ORANGE", "pear", "Banana"}'
    assert evaluate(f"List.Distinct({source}, Comparer.OrdinalIgnoreCase)") == [
        "apple",
        "Pear",
        "banana",
        "ORANGE",
    ]


def test_distinct_takes_a_selector_and_comparer_pair() -> None:
    """Example 4's `{each _{0}, Comparer.OrdinalIgnoreCase}` shape."""
    source = '{{"USA", 1}, {"canada", 2}, {"Usa", 3}, {"CANADA", 4}}'
    assert evaluate(
        f"List.Distinct({source}, {{each _{{0}}, Comparer.OrdinalIgnoreCase}})"
    ) == [["USA", 1], ["canada", 2]]


def test_contains_takes_a_comparer() -> None:
    """Example 3. Case-insensitive membership is a real, common query."""
    source = '{"Pears", "Bananas", "Rhubarb", "Peaches"}'
    assert evaluate(f'List.Contains({source}, "rhubarb", Comparer.OrdinalIgnoreCase)')
    assert not evaluate(f'List.Contains({source}, "rhubarb")')


# --------------------------------------------------------------------------
# List.Max / List.Min - default, comparisonCriteria, includeNulls
# --------------------------------------------------------------------------


def test_max_keys_through_a_transform_and_returns_the_original_value() -> None:
    """List.Max Example 4, reduced to a culture pqtools implements.

    The point of the example is that comparisonCriteria transforms values
    *before* comparing but the ORIGINAL item is returned - the page's output
    is the German date string, not the date it parsed to.
    """
    source = '{"2024-02-01", "2025-05-15", "2021-10-10"}'
    assert evaluate(f"List.Max({source}, null, each Date.FromText(_))") == "2025-05-15"


def test_min_and_max_include_nulls_by_default() -> None:
    """Derived, and worth stating because no Microsoft example shows it.

    The page says "includeNulls ... The default value is true", and M orders
    null below every other value. Those two documented facts together mean
    the minimum of a list containing a null IS null. Nothing here is a guess
    about behaviour, but nothing here is a worked example either - if this
    ever proves wrong, this is the assumption to revisit.
    """
    assert evaluate("List.Min({1, null, 3})") is None
    assert evaluate("List.Max({1, null, 3})") == 3


def test_include_nulls_false_drops_them() -> None:
    assert evaluate("List.Min({1, null, 3}, null, null, false)") == 1
    assert evaluate("List.Min({null, null}, -1, null, false)") == -1


def test_max_refuses_an_order_value_rather_than_inventing_one() -> None:
    """List.Max's page defines comparisonCriteria only as a transform.

    An Order value has no documented meaning for a maximum, so it is refused
    by name instead of being quietly interpreted as "return the minimum".
    """
    with pytest.raises(UnsupportedError, match="no Order form"):
        evaluate("List.Max({1, 2, 3}, null, Order.Descending)")


# --------------------------------------------------------------------------
# List.Sum / List.Average - the documented domains
# --------------------------------------------------------------------------


def test_sum_skips_nulls_and_is_null_when_there_are_none() -> None:
    """Both halves are quoted verbatim from the page, and both were wrong.

    `[Amount]` summed over a column with one blank cell raised. That is the
    single most ordinary thing a Power Query user does with List.Sum.
    """
    assert evaluate("List.Sum({1, null, 3})") == 4
    assert evaluate("List.Sum({})") is None
    assert evaluate("List.Sum({null})") is None


def test_average_of_dates_returns_a_date() -> None:
    """Example 2 verbatim: the result keeps the input's datatype."""
    assert evaluate(
        "List.Average({#date(2011, 1, 1), #date(2011, 1, 2), #date(2011, 1, 3)})"
    ) == datetime.date(2011, 1, 2)


def test_average_of_durations_and_times() -> None:
    assert evaluate(
        "List.Average({#duration(0, 1, 0, 0), #duration(0, 3, 0, 0)})"
    ) == datetime.timedelta(hours=2)
    assert evaluate("List.Average({#time(1, 0, 0), #time(3, 0, 0)})") == datetime.time(
        2, 0
    )


def test_average_refuses_a_fractional_day_rather_than_guessing() -> None:
    """The one case the reference genuinely does not define.

    A date's unit is a whole day, and the page says only that the result
    keeps the input datatype - it gives no rounding rule and no datetime
    fallback. Returning either one would be inventing an answer, so the
    ambiguous case names itself instead.
    """
    with pytest.raises(UnsupportedError, match="fractional day"):
        evaluate("List.Average({#date(2011, 1, 1), #date(2011, 1, 2)})")


def test_average_refuses_a_mixed_list() -> None:
    with pytest.raises(Exception, match="List.Average only works with"):
        evaluate("List.Average({1, #date(2011, 1, 1)})")


# --------------------------------------------------------------------------
# List.Percentile - PercentileMode
# --------------------------------------------------------------------------


def test_percentile_default_is_excel_inc() -> None:
    assert evaluate("List.Percentile({5, 3, 1, 7, 9}, 0.25)") == 3


def test_percentile_excel_exc_matches_the_documented_output() -> None:
    """Example 2 verbatim. ExcelExc ranks over n + 1 positions, not n - 1."""
    assert evaluate(
        "List.Percentile({5, 3, 1, 7, 9}, {0.25, 0.5, 0.75}, "
        "[PercentileMode=PercentileMode.ExcelExc])"
    ) == [2, 5, 8]


def test_percentile_sql_disc_never_interpolates() -> None:
    """PERCENTILE_DISC returns a member of the list; PERCENTILE_CONT does not.

    Pinning both together is the point: they differ only for percentiles
    that fall between two values, which is where a wrong mode is invisible.
    """
    source = "{1, 3, 5, 7, 9}"
    disc = "[PercentileMode=PercentileMode.SqlDisc]"
    cont = "[PercentileMode=PercentileMode.SqlCont]"
    assert evaluate(f"List.Percentile({source}, 0.3, {disc})") == 3
    assert evaluate(f"List.Percentile({source}, 0.3, {cont})") == 3.4


def test_percentile_excel_exc_refuses_out_of_range_percentiles() -> None:
    """Excel returns #NUM! outside 1/(n+1)..n/(n+1); clamping would lie."""
    with pytest.raises(Exception, match="ExcelExc is undefined"):
        evaluate(
            "List.Percentile({1, 2, 3}, 0.1, [PercentileMode=PercentileMode.ExcelExc])"
        )


def test_percentile_rejects_an_unknown_option() -> None:
    with pytest.raises(UnsupportedError, match="option"):
        evaluate("List.Percentile({1, 2, 3}, 0.5, [Mode=1])")
