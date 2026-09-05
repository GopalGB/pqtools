"""Temporal values must compare by value, in both `=` and `<`.

Regression guard for a silent-wrongness bug found on 2026-09-03: `_m_equal` and
`_eval_relational` predated date support and recognised only number/text/list/
record, so a date compared FALSE to itself. The visible symptom was not an error
- `Table.SelectRows(t, each [d] = #date(2024,1,1))` returned an EMPTY TABLE. A
date filter, one of the most common things a real query does, silently discarded
every row. Tests here assert the values, not the absence of an exception.
"""

from __future__ import annotations

import pytest

from pqtools.evaluate import EvalError, evaluate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("#date(2024,1,1) = #date(2024,1,1)", True),
        ("#date(2024,1,1) = #date(2024,1,2)", False),
        ("#date(2024,1,1) <> #date(2024,1,1)", False),
        ("#datetime(2024,1,1,9,0,0) = #datetime(2024,1,1,9,0,0)", True),
        ("#datetime(2024,1,1,9,0,0) = #datetime(2024,1,1,9,0,1)", False),
        ("#time(9,30,0) = #time(9,30,0)", True),
        ("#duration(1,0,0,0) = #duration(1,0,0,0)", True),
        ("#duration(1,0,0,0) = #duration(0,1,0,0)", False),
    ],
)
def test_temporal_equality(expression: str, expected: bool) -> None:
    assert evaluate(expression) is expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("#date(2024,1,1) < #date(2024,6,1)", True),
        ("#date(2024,6,1) < #date(2024,1,1)", False),
        ("#date(2024,1,1) <= #date(2024,1,1)", True),
        ("#date(2024,6,1) > #date(2024,1,1)", True),
        ("#time(9,0,0) < #time(17,0,0)", True),
        ("#duration(1,0,0,0) > #duration(0,1,0,0)", True),
        ("#datetime(2024,1,1,9,0,0) < #datetime(2024,1,1,17,0,0)", True),
    ],
)
def test_temporal_ordering(expression: str, expected: bool) -> None:
    assert evaluate(expression) is expected


def test_date_filter_actually_selects_the_matching_row() -> None:
    """The exact shape that silently returned an empty table."""
    rows = evaluate(
        "Table.SelectRows("
        "Table.FromRecords({[d=#date(2024,1,1)],[d=#date(2025,1,1)]}), "
        "each [d] = #date(2024,1,1))"
    )
    assert len(rows) == 1
    assert rows[0]["d"].isoformat() == "2024-01-01"


def test_date_range_filter_selects_the_later_row() -> None:
    rows = evaluate(
        "Table.SelectRows("
        "Table.FromRecords({[d=#date(2024,1,1)],[d=#date(2025,1,1)]}), "
        "each [d] >= #date(2024,6,1))"
    )
    assert len(rows) == 1
    assert rows[0]["d"].isoformat() == "2025-01-01"


def test_a_date_and_a_number_are_not_equal_rather_than_an_error() -> None:
    """M's `=` is total: mismatched types are unequal, they do not raise."""
    assert evaluate("#date(2024,1,1) = 5") is False


def test_ordering_a_date_against_a_number_is_refused() -> None:
    """Ordering, unlike equality, is a type error rather than a silent answer."""
    with pytest.raises(EvalError, match="relational operators require"):
        evaluate("#date(2024,1,1) < 5")


def test_a_date_and_a_datetime_are_different_kinds() -> None:
    """datetime.datetime subclasses datetime.date in Python; M treats them as
    distinct types, so they must not compare equal by subclass accident."""
    assert evaluate("#date(2024,1,1) = #datetime(2024,1,1,0,0,0)") is False


def test_the_is_in_family_answers_one_question_one_way() -> None:
    """Date.IsInCurrentMonth and Date.IsInPreviousMonth used to both say yes.

    The two oldest members of the family read an aware argument in the
    VALUE's own offset; the 38 added later normalise it to the system's
    local wall clock first, which is what "as determined by the current date
    and time on the system" says to do. At a far-eastern offset the two
    readings land in different months, and the family reported a single
    value as being in the current month AND the previous one.

    The test does not assert which month - that depends on today - only that
    the family cannot contradict itself, which is true on every date.
    """
    import datetime as pydt

    from pqtools.builtins import _datetime as module

    far_east = "#datetimezone(2026,9,1,0,30,0,14,0)"
    # Freeze the clock so the assertion is about the offset, not about when
    # the suite happens to run.
    local = pydt.datetime(2026, 9, 1, 10, 0, 0)
    original = module._now_local
    module._now_local = lambda: local  # type: ignore[assignment]
    try:
        current = evaluate(f"Date.IsInCurrentMonth({far_east})")
        previous = evaluate(f"Date.IsInPreviousMonth({far_east})")
    finally:
        module._now_local = original  # type: ignore[assignment]
    # Exact values, not just "not both true": asserting only the absence of
    # the contradiction would pass by luck in any month where the old code
    # also happened to say False.
    assert (current, previous) == (False, True), (
        "at UTC+14 this value is 2026-08-31 in system-local time, so with "
        "the clock frozen to 2026-09-01 it is in the PREVIOUS month and not "
        f"the current one; got current={current}, previous={previous}"
    )


def test_a_naive_value_is_unaffected_by_that_change() -> None:
    # The normalisation only moves aware values; the ordinary case must not
    # have shifted underneath anyone.
    assert evaluate("Date.IsInCurrentYear(Date.From(DateTime.LocalNow()))") is True
