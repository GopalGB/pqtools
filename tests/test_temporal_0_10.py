"""Date.*, DateTime.*, DateTimeZone.*, Time.* and Duration.* names that were
missing until 0.10.0, plus the two end-of-period defects found while adding
them.

The name list is not from memory: every function tested here appears in one
of the five Microsoft Learn function-index pages (date-functions,
datetime-functions, datetimezone-functions, time-functions,
duration-functions), and `test_every_documented_name_is_registered` pins that
whole list as a contract.

Expected values are derived, not typed from memory: calendar facts come from
Python's own `datetime`/`calendar`, zone conversions from Python's own
`astimezone`, and the FILETIME conversion from the Unix-epoch identity. Where
a doc example IS the evidence for a rule, the example is reproduced verbatim
and cited; where the docs leave an edge case open, the test says so and pins
the choice rather than implying it was verified.

Clock-reading functions (DateTime/DateTimeZone LocalNow/FixedLocalNow/UtcNow/
FixedUtcNow, DateTime.FromFileTime's local conversion) are never asserted on
an exact instant - only type, tzinfo, a bounded range, or an independent
same-host recomputation. The relative-period predicates (IsInCurrent*/IsInNext*/
IsInPrevious*) run against a FROZEN clock, because a test that builds
"now + 1 second" and races the evaluator's own clock read is flaky by
construction.
"""

from __future__ import annotations

import calendar
import datetime as pydt

import pytest

from pqtools.builtins import _datetime as temporal
from pqtools.evaluate import BUILTINS, EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# M-literal builders - tests are written the way real M is written
# --------------------------------------------------------------------------


def _d(value: pydt.date) -> str:
    return f"#date({value.year},{value.month},{value.day})"


def _dt(value: pydt.datetime) -> str:
    seconds = value.second + value.microsecond / 1_000_000
    return (
        f"#datetime({value.year},{value.month},{value.day},"
        f"{value.hour},{value.minute},{seconds})"
    )


def _dtz(value: pydt.datetime) -> str:
    offset = value.utcoffset()
    assert offset is not None
    total = round(offset.total_seconds() / 60)
    sign = -1 if total < 0 else 1
    hours, minutes = divmod(abs(total), 60)
    seconds = value.second + value.microsecond / 1_000_000
    return (
        f"#datetimezone({value.year},{value.month},{value.day},"
        f"{value.hour},{value.minute},{seconds},{sign * hours},{sign * minutes})"
    )


# A Thursday in Q2, deliberately mid-month and mid-year so that every
# period-offset test has room on both sides.
FROZEN = pydt.datetime(2026, 5, 14, 13, 45, 30, 123456)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> pydt.datetime:
    monkeypatch.setattr(temporal, "_now_local", lambda: FROZEN)
    return FROZEN


def test_frozen_clock_anchor_is_what_the_offset_tests_assume() -> None:
    # Guards every hand-derived expectation below (Q2, mid-May, a Thursday).
    assert FROZEN.date() == pydt.date(2026, 5, 14)
    assert FROZEN.weekday() == 3  # Python: Thursday
    assert (FROZEN.month - 1) // 3 + 1 == 2  # Q2


# --------------------------------------------------------------------------
# The documented contract
# --------------------------------------------------------------------------

DOCUMENTED = {
    # https://learn.microsoft.com/en-us/powerquery-m/date-functions
    "Date.AddDays",
    "Date.AddMonths",
    "Date.AddQuarters",
    "Date.AddWeeks",
    "Date.AddYears",
    "Date.Day",
    "Date.DayOfWeek",
    "Date.DayOfWeekName",
    "Date.DayOfYear",
    "Date.DaysInMonth",
    "Date.EndOfDay",
    "Date.EndOfMonth",
    "Date.EndOfQuarter",
    "Date.EndOfWeek",
    "Date.EndOfYear",
    "Date.From",
    "Date.FromText",
    "Date.IsInCurrentDay",
    "Date.IsInCurrentMonth",
    "Date.IsInCurrentQuarter",
    "Date.IsInCurrentWeek",
    "Date.IsInCurrentYear",
    "Date.IsInNextDay",
    "Date.IsInNextMonth",
    "Date.IsInNextNDays",
    "Date.IsInNextNMonths",
    "Date.IsInNextNQuarters",
    "Date.IsInNextNWeeks",
    "Date.IsInNextNYears",
    "Date.IsInNextQuarter",
    "Date.IsInNextWeek",
    "Date.IsInNextYear",
    "Date.IsInPreviousDay",
    "Date.IsInPreviousMonth",
    "Date.IsInPreviousNDays",
    "Date.IsInPreviousNMonths",
    "Date.IsInPreviousNQuarters",
    "Date.IsInPreviousNWeeks",
    "Date.IsInPreviousNYears",
    "Date.IsInPreviousQuarter",
    "Date.IsInPreviousWeek",
    "Date.IsInPreviousYear",
    "Date.IsInYearToDate",
    "Date.IsLeapYear",
    "Date.Month",
    "Date.MonthName",
    "Date.QuarterOfYear",
    "Date.StartOfDay",
    "Date.StartOfMonth",
    "Date.StartOfQuarter",
    "Date.StartOfWeek",
    "Date.StartOfYear",
    "Date.ToRecord",
    "Date.ToText",
    "Date.WeekOfMonth",
    "Date.WeekOfYear",
    "Date.Year",
    "#date",
    # https://learn.microsoft.com/en-us/powerquery-m/datetime-functions
    "DateTime.AddZone",
    "DateTime.Date",
    "DateTime.FixedLocalNow",
    "DateTime.From",
    "DateTime.FromFileTime",
    "DateTime.FromText",
    "DateTime.IsInCurrentHour",
    "DateTime.IsInCurrentMinute",
    "DateTime.IsInCurrentSecond",
    "DateTime.IsInNextHour",
    "DateTime.IsInNextMinute",
    "DateTime.IsInNextNHours",
    "DateTime.IsInNextNMinutes",
    "DateTime.IsInNextNSeconds",
    "DateTime.IsInNextSecond",
    "DateTime.IsInPreviousHour",
    "DateTime.IsInPreviousMinute",
    "DateTime.IsInPreviousNHours",
    "DateTime.IsInPreviousNMinutes",
    "DateTime.IsInPreviousNSeconds",
    "DateTime.IsInPreviousSecond",
    "DateTime.LocalNow",
    "DateTime.Time",
    "DateTime.ToRecord",
    "DateTime.ToText",
    "#datetime",
    # https://learn.microsoft.com/en-us/powerquery-m/datetimezone-functions
    "DateTimeZone.FixedLocalNow",
    "DateTimeZone.FixedUtcNow",
    "DateTimeZone.From",
    "DateTimeZone.FromFileTime",
    "DateTimeZone.FromText",
    "DateTimeZone.LocalNow",
    "DateTimeZone.RemoveZone",
    "DateTimeZone.SwitchZone",
    "DateTimeZone.ToLocal",
    "DateTimeZone.ToRecord",
    "DateTimeZone.ToText",
    "DateTimeZone.ToUtc",
    "DateTimeZone.UtcNow",
    "DateTimeZone.ZoneHours",
    "DateTimeZone.ZoneMinutes",
    "#datetimezone",
    # https://learn.microsoft.com/en-us/powerquery-m/time-functions
    "Time.EndOfHour",
    "Time.From",
    "Time.FromText",
    "Time.Hour",
    "Time.Minute",
    "Time.Second",
    "Time.StartOfHour",
    "Time.ToRecord",
    "Time.ToText",
    "#time",
    # https://learn.microsoft.com/en-us/powerquery-m/duration-functions
    "Duration.Days",
    "Duration.From",
    "Duration.FromText",
    "Duration.Hours",
    "Duration.Minutes",
    "Duration.Seconds",
    "Duration.ToRecord",
    "Duration.TotalDays",
    "Duration.TotalHours",
    "Duration.TotalMinutes",
    "Duration.TotalSeconds",
    "Duration.ToText",
    "#duration",
}


def test_every_documented_name_is_registered() -> None:
    assert sorted(name for name in DOCUMENTED if name not in BUILTINS) == []


# --------------------------------------------------------------------------
# Date.StartOf* / EndOf* - including the two defects this work found
# --------------------------------------------------------------------------

LAST_MICROSECOND = pydt.time(23, 59, 59, 999_999)


def _last_instant_of(day: pydt.date) -> pydt.datetime:
    """One microsecond before the following midnight, derived not typed."""
    return pydt.datetime.combine(day + pydt.timedelta(days=1), pydt.time()) - (
        pydt.timedelta(microseconds=1)
    )


def test_start_of_day_matches_the_docs_example() -> None:
    # docs: Date.StartOfDay(#datetime(2011,10,10,8,0,0)) -> #datetime(2011,10,10,0,0,0)
    assert evaluate("Date.StartOfDay(#datetime(2011,10,10,8,0,0))") == pydt.datetime(
        2011, 10, 10, 0, 0, 0
    )


def test_end_of_day_matches_the_docs_example_to_the_representable_ceiling() -> None:
    # docs: -> #datetime(2011,5,14,23,59,59.9999999). A datetime carries
    # microseconds, so 999999 is the closest instant this value model holds;
    # the 7th fractional digit is structurally unavailable, not rounded away.
    result = evaluate("Date.EndOfDay(#datetime(2011,5,14,17,0,0))")
    assert result == _last_instant_of(pydt.date(2011, 5, 14))
    assert result.time() == LAST_MICROSECOND


def test_end_of_day_preserves_the_zone_of_a_datetimezone() -> None:
    # docs example 2: Date.EndOfDay(#datetimezone(2011,5,17,5,0,0,-7,0))
    #                 -> #datetimezone(2011,5,17,23,59,59.9999999,-7,0)
    result = evaluate("Date.EndOfDay(#datetimezone(2011,5,17,5,0,0,-7,0))")
    assert result.utcoffset() == pydt.timedelta(hours=-7)
    assert result.replace(tzinfo=None) == _last_instant_of(pydt.date(2011, 5, 17))


def test_start_of_day_of_a_plain_date_stays_a_plain_date() -> None:
    result = evaluate("Date.StartOfDay(#date(2024,3,9))")
    assert result == pydt.date(2024, 3, 9)
    assert type(result) is pydt.date


def test_end_of_day_of_a_plain_date_stays_a_plain_date() -> None:
    # A date has no time-of-day to push to 23:59:59 - matching
    # Date.EndOfMonth(#date(2011,5,14)) -> #date(2011,5,31) in the docs.
    result = evaluate("Date.EndOfDay(#date(2024,3,9))")
    assert result == pydt.date(2024, 3, 9)
    assert type(result) is pydt.date


def test_end_of_month_on_a_datetime_is_the_last_instant_not_midnight() -> None:
    # REGRESSION: this returned midnight of the right day before 0.10.0,
    # contradicting the docs' own #datetimezone(2011,5,31,23,59,59.9999999,...)
    # example. No prior test covered a datetime argument, so the defect was
    # invisible.
    result = evaluate("Date.EndOfMonth(#datetime(2011,5,17,5,0,0))")
    assert result == _last_instant_of(pydt.date(2011, 5, 31))


def test_end_of_month_on_a_datetimezone_keeps_its_zone() -> None:
    # REGRESSION: the zone was silently dropped (datetimezone in, naive
    # datetime out) before 0.10.0.
    result = evaluate("Date.EndOfMonth(#datetimezone(2011,5,17,5,0,0,-7,0))")
    assert result.utcoffset() == pydt.timedelta(hours=-7)
    assert result.replace(tzinfo=None) == _last_instant_of(pydt.date(2011, 5, 31))


def test_end_of_year_on_a_datetime_matches_the_docs_example() -> None:
    # docs: Date.EndOfYear(#datetime(2011,5,14,17,0,0))
    #       -> #datetime(2011,12,31,23,59,59.9999999)
    assert evaluate("Date.EndOfYear(#datetime(2011,5,14,17,0,0))") == _last_instant_of(
        pydt.date(2011, 12, 31)
    )


def test_end_of_week_on_a_datetimezone_matches_the_docs_example() -> None:
    # docs: Date.EndOfWeek(#datetimezone(2011,5,17,5,0,0,-7,0), Day.Sunday)
    #       -> #datetimezone(2011,5,21,23,59,59.9999999,-7,0)
    result = evaluate("Date.EndOfWeek(#datetimezone(2011,5,17,5,0,0,-7,0), Day.Sunday)")
    assert result.utcoffset() == pydt.timedelta(hours=-7)
    assert result.replace(tzinfo=None) == _last_instant_of(pydt.date(2011, 5, 21))


def test_end_of_week_of_a_plain_date_matches_the_docs_example() -> None:
    # docs: Date.EndOfWeek(#date(2011,5,14)) -> #date(2011,5,14) - the 14th
    # was itself a Saturday, the last day of a Sunday-first week.
    assert pydt.date(2011, 5, 14).weekday() == 5  # Python: Saturday
    assert evaluate("Date.EndOfWeek(#date(2011,5,14))") == pydt.date(2011, 5, 14)


def test_start_of_month_on_a_datetimezone_keeps_its_zone() -> None:
    # REGRESSION alongside the End* fix: Start* kept midnight (correct per the
    # docs) but dropped tzinfo.
    result = evaluate("Date.StartOfMonth(#datetimezone(2011,5,17,5,0,0,-7,0))")
    assert result.utcoffset() == pydt.timedelta(hours=-7)
    assert result.replace(tzinfo=None) == pydt.datetime(2011, 5, 1)


def test_start_of_quarter_matches_the_docs_example() -> None:
    # docs: Date.StartOfQuarter(#datetime(2011,10,10,8,0,0))
    #       -> #datetime(2011,10,1,0,0,0)
    assert evaluate("Date.StartOfQuarter(#datetime(2011,10,10,8,0,0))") == (
        pydt.datetime(2011, 10, 1)
    )


def test_end_of_quarter_matches_the_docs_example() -> None:
    # docs: Date.EndOfQuarter(#datetime(2011,10,10,8,0,0))
    #       -> #datetime(2011,12,31,23,59,59.9999999)
    assert evaluate("Date.EndOfQuarter(#datetime(2011,10,10,8,0,0))") == (
        _last_instant_of(pydt.date(2011, 12, 31))
    )


@pytest.mark.parametrize("month", list(range(1, 13)))
def test_quarter_bounds_are_a_three_month_block_containing_the_input(
    month: int,
) -> None:
    # Checked as properties rather than by re-deriving the same formula: the
    # start is the 1st of a quarter-opening month, the end is the last day of
    # a quarter-closing month, and the input sits inside.
    source_date = pydt.date(2024, month, 15)
    start = evaluate(f"Date.StartOfQuarter({_d(source_date)})")
    end = evaluate(f"Date.EndOfQuarter({_d(source_date)})")
    assert start.day == 1
    assert start.month in (1, 4, 7, 10)
    assert end.month in (3, 6, 9, 12)
    assert end.day == calendar.monthrange(end.year, end.month)[1]
    assert start <= source_date <= end
    assert (end.month - start.month) == 2


# --------------------------------------------------------------------------
# Date.AddQuarters / DaysInMonth / IsLeapYear / WeekOfMonth / ToRecord
# --------------------------------------------------------------------------


def test_add_quarters_matches_the_docs_example() -> None:
    # docs: Date.AddQuarters(#date(2011,5,14), 1) -> #date(2011,8,14)
    assert evaluate("Date.AddQuarters(#date(2011,5,14), 1)") == pydt.date(2011, 8, 14)


@pytest.mark.parametrize("quarters", [-5, -1, 0, 1, 3, 8])
def test_add_quarters_is_three_add_months(quarters: int) -> None:
    source = "#date(2024,2,29)"
    assert evaluate(f"Date.AddQuarters({source}, {quarters})") == evaluate(
        f"Date.AddMonths({source}, {quarters * 3})"
    )


def test_add_quarters_clamps_to_the_shorter_target_month() -> None:
    # 2024-11-30 + 1 quarter lands in February; February 2025 has 28 days.
    expected_day = calendar.monthrange(2025, 2)[1]
    assert evaluate("Date.AddQuarters(#date(2024,11,30), 1)") == pydt.date(
        2025, 2, expected_day
    )


def test_add_quarters_keeps_the_datetime_shape() -> None:
    assert evaluate("Date.AddQuarters(#datetime(2024,1,31,10,30,0), 1)") == (
        pydt.datetime(2024, 4, 30, 10, 30)
    )


def test_days_in_month_matches_the_docs_example() -> None:
    # docs: Date.DaysInMonth(#date(2011,12,1)) -> 31
    assert evaluate("Date.DaysInMonth(#date(2011,12,1))") == 31


@pytest.mark.parametrize(
    ("year", "month"), [(2024, 2), (2023, 2), (2024, 4), (2024, 12), (1900, 2)]
)
def test_days_in_month_agrees_with_pythons_calendar(year: int, month: int) -> None:
    expected = calendar.monthrange(year, month)[1]
    assert evaluate(f"Date.DaysInMonth({_d(pydt.date(year, month, 1))})") == expected


def test_is_leap_year_matches_the_docs_example() -> None:
    # docs: Date.IsLeapYear(#date(2012,1,1)) -> true
    assert evaluate("Date.IsLeapYear(#date(2012,1,1))") is True


@pytest.mark.parametrize("year", [1900, 2000, 2023, 2024, 2100])
def test_is_leap_year_agrees_with_pythons_calendar(year: int) -> None:
    assert evaluate(f"Date.IsLeapYear({_d(pydt.date(year, 1, 1))})") is calendar.isleap(
        year
    )


def test_week_of_month_matches_the_docs_example() -> None:
    # docs: Date.WeekOfMonth(#date(2011,3,15)) -> 3
    assert evaluate("Date.WeekOfMonth(#date(2011,3,15))") == 3


@pytest.mark.parametrize("day", [1, 6, 7, 15, 28, 31])
def test_week_of_month_counts_sunday_starts_since_the_first(day: int) -> None:
    # Independent derivation: how many Sunday-first week blocks have begun,
    # counting the (possibly partial) block the 1st falls in as week 1.
    target = pydt.date(2024, 1, day)
    first = pydt.date(2024, 1, 1)
    sundays_before = sum(
        1
        for offset in range(1, (target - first).days + 1)
        if (first + pydt.timedelta(days=offset)).weekday() == 6
    )
    assert evaluate(f"Date.WeekOfMonth({_d(target)})") == sundays_before + 1


def test_week_of_month_honours_an_explicit_first_day_of_week() -> None:
    # 2024-01-01 was itself a Monday, so a Monday-first week puts the 7th
    # (Sunday) still in week 1 while a Sunday-first week has already moved on.
    assert pydt.date(2024, 1, 1).weekday() == 0
    assert evaluate("Date.WeekOfMonth(#date(2024,1,7), Day.Monday)") == 1
    assert evaluate("Date.WeekOfMonth(#date(2024,1,7))") == 2


def test_date_to_record_matches_the_docs_example() -> None:
    # docs: Date.ToRecord(#date(2011,12,31)) -> [Year=2011, Month=12, Day=31]
    assert evaluate("Date.ToRecord(#date(2011,12,31))") == {
        "Year": 2011,
        "Month": 12,
        "Day": 31,
    }


def test_date_to_record_accepts_datetime_like() -> None:
    # CHOICE, not a verified fact: the docs declare this parameter `date as
    # date` (strict), unlike every sibling Date.* accessor's `dateTime as
    # any`, and no example shows a datetime argument. This module reuses the
    # same permissive coercion as its siblings rather than adding a second,
    # untested type-checking path.
    assert evaluate("Date.ToRecord(#datetime(2011,12,31,11,56,2))") == {
        "Year": 2011,
        "Month": 12,
        "Day": 31,
    }


# --------------------------------------------------------------------------
# Date.IsIn{Current,Next,Previous}* - against a frozen clock
# --------------------------------------------------------------------------


def _date_at_period_offset(unit: str, offset: int) -> pydt.date:
    base = FROZEN.date()
    if unit == "day":
        return base + pydt.timedelta(days=offset)
    if unit == "week":
        # Exactly 7 days is exactly one week block, whatever weekday `base` is.
        return base + pydt.timedelta(weeks=offset)
    if unit == "month":
        index = base.year * 12 + (base.month - 1) + offset
        year, month0 = divmod(index, 12)
        return pydt.date(year, month0 + 1, 1)
    if unit == "quarter":
        index = base.year * 4 + (base.month - 1) // 3 + offset
        year, quarter = divmod(index, 4)
        return pydt.date(year, quarter * 3 + 1, 1)
    return pydt.date(base.year + offset, base.month, base.day)  # year


def test_period_offset_helper_lands_where_hand_derivation_says(
    frozen_clock: pydt.datetime,
) -> None:
    # The helper above is what every parametrised case below leans on, so it
    # gets its own hand-derived check against the fixed 2026-05-14 anchor.
    assert _date_at_period_offset("day", 1) == pydt.date(2026, 5, 15)
    assert _date_at_period_offset("week", -1) == pydt.date(2026, 5, 7)
    assert _date_at_period_offset("month", 1) == pydt.date(2026, 6, 1)
    assert _date_at_period_offset("quarter", 1) == pydt.date(2026, 7, 1)
    assert _date_at_period_offset("quarter", -1) == pydt.date(2026, 1, 1)
    assert _date_at_period_offset("year", 1) == pydt.date(2027, 5, 14)


DATE_UNITS = [("Day", "day"), ("Week", "week"), ("Quarter", "quarter")]
DATE_ALL_UNITS = DATE_UNITS + [("Month", "month"), ("Year", "year")]


@pytest.mark.parametrize(("label", "unit"), DATE_UNITS)
def test_date_is_in_current_period(
    frozen_clock: pydt.datetime, label: str, unit: str
) -> None:
    call = f"Date.IsInCurrent{label}"
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 0))})") is True
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 1))})") is False
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, -1))})") is False


@pytest.mark.parametrize(("label", "unit"), DATE_ALL_UNITS)
def test_date_is_in_next_period(
    frozen_clock: pydt.datetime, label: str, unit: str
) -> None:
    call = f"Date.IsInNext{label}"
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 1))})") is True
    # Docs, every page in this family: "returns false when passed a value
    # that occurs within the current [period]".
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 0))})") is False
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 2))})") is False


@pytest.mark.parametrize(("label", "unit"), DATE_ALL_UNITS)
def test_date_is_in_next_n_periods(
    frozen_clock: pydt.datetime, label: str, unit: str
) -> None:
    call = f"Date.IsInNextN{label}s"
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 1))}, 2)") is True
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 2))}, 2)") is True
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 3))}, 2)") is False
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 0))}, 2)") is False
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, -1))}, 2)") is False


@pytest.mark.parametrize(("label", "unit"), DATE_ALL_UNITS)
def test_date_is_in_previous_period(
    frozen_clock: pydt.datetime, label: str, unit: str
) -> None:
    call = f"Date.IsInPrevious{label}"
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, -1))})") is True
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 0))})") is False
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, -2))})") is False


@pytest.mark.parametrize(("label", "unit"), DATE_ALL_UNITS)
def test_date_is_in_previous_n_periods(
    frozen_clock: pydt.datetime, label: str, unit: str
) -> None:
    call = f"Date.IsInPreviousN{label}s"
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, -1))}, 2)") is True
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, -2))}, 2)") is True
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, -3))}, 2)") is False
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 0))}, 2)") is False
    assert evaluate(f"{call}({_d(_date_at_period_offset(unit, 1))}, 2)") is False


def test_date_is_in_next_day_reproduces_the_docs_example(
    frozen_clock: pydt.datetime,
) -> None:
    # docs: Date.IsInNextDay(Date.AddDays(DateTime.FixedLocalNow(), 1)) -> true.
    # FixedLocalNow is not frozen (it reads the clock directly), so the
    # equivalent frozen-clock form is used: tomorrow, as M would write it.
    assert evaluate(f"Date.IsInNextDay(Date.AddDays({_d(FROZEN.date())}, 1))") is True


def test_date_is_in_year_to_date(frozen_clock: pydt.datetime) -> None:
    today = FROZEN.date()
    assert evaluate(f"Date.IsInYearToDate({_d(today)})") is True
    assert evaluate(f"Date.IsInYearToDate({_d(pydt.date(today.year, 1, 1))})") is True
    assert evaluate(f"Date.IsInYearToDate({_d(today + pydt.timedelta(days=1))})") is (
        False
    )
    assert evaluate(
        f"Date.IsInYearToDate({_d(pydt.date(today.year - 1, 12, 31))})"
    ) is (False)


def _alternate_zone(local_offset: pydt.timedelta) -> pydt.timezone:
    """A valid fixed offset 14 hours away from the host's - far enough that
    the same instant falls on a different calendar date."""
    shifted = local_offset - pydt.timedelta(hours=14)
    if shifted <= pydt.timedelta(hours=-23):
        shifted = local_offset + pydt.timedelta(hours=14)
    return pydt.timezone(shifted)


def test_is_in_normalises_an_aware_argument_to_system_local_time(
    frozen_clock: pydt.datetime,
) -> None:
    # CHOICE, not a verified fact: no fetched docs example passes a
    # datetimezone to this family. Every page says the comparison is "as
    # determined by the current date and time on the system", which this
    # module reads as: convert an aware value into the SYSTEM's local frame
    # first, rather than comparing the value's own wall-clock fields.
    tomorrow_local = FROZEN + pydt.timedelta(days=1)
    local_offset = tomorrow_local.astimezone().utcoffset()
    assert local_offset is not None
    aware_local = tomorrow_local.replace(tzinfo=pydt.timezone(local_offset))
    elsewhere = aware_local.astimezone(_alternate_zone(local_offset))
    # The test only proves anything if the raw fields DISAGREE with the
    # local date - otherwise both readings would answer the same way.
    assert elsewhere.date() != tomorrow_local.date()
    assert evaluate(f"Date.IsInNextDay({_dtz(elsewhere)})") is True


# --------------------------------------------------------------------------
# DateTime.IsIn{Current,Next,Previous}{Hour,Minute,Second}[N]
# --------------------------------------------------------------------------

TIME_UNITS = [
    ("Hour", pydt.timedelta(hours=1)),
    ("Minute", pydt.timedelta(minutes=1)),
    ("Second", pydt.timedelta(seconds=1)),
]


@pytest.mark.parametrize(("label", "step"), TIME_UNITS)
def test_datetime_is_in_current_unit(
    frozen_clock: pydt.datetime, label: str, step: pydt.timedelta
) -> None:
    call = f"DateTime.IsInCurrent{label}"
    assert evaluate(f"{call}({_dt(FROZEN)})") is True
    assert evaluate(f"{call}({_dt(FROZEN + step)})") is False
    assert evaluate(f"{call}({_dt(FROZEN - step)})") is False


@pytest.mark.parametrize(("label", "step"), TIME_UNITS)
def test_datetime_is_in_next_unit(
    frozen_clock: pydt.datetime, label: str, step: pydt.timedelta
) -> None:
    call = f"DateTime.IsInNext{label}"
    assert evaluate(f"{call}({_dt(FROZEN + step)})") is True
    assert evaluate(f"{call}({_dt(FROZEN)})") is False
    assert evaluate(f"{call}({_dt(FROZEN + 2 * step)})") is False


@pytest.mark.parametrize(("label", "step"), TIME_UNITS)
def test_datetime_is_in_next_n_units(
    frozen_clock: pydt.datetime, label: str, step: pydt.timedelta
) -> None:
    call = f"DateTime.IsInNextN{label}s"
    assert evaluate(f"{call}({_dt(FROZEN + 2 * step)}, 2)") is True
    assert evaluate(f"{call}({_dt(FROZEN + 3 * step)}, 2)") is False
    assert evaluate(f"{call}({_dt(FROZEN)}, 2)") is False


@pytest.mark.parametrize(("label", "step"), TIME_UNITS)
def test_datetime_is_in_previous_unit(
    frozen_clock: pydt.datetime, label: str, step: pydt.timedelta
) -> None:
    call = f"DateTime.IsInPrevious{label}"
    assert evaluate(f"{call}({_dt(FROZEN - step)})") is True
    assert evaluate(f"{call}({_dt(FROZEN)})") is False
    assert evaluate(f"{call}({_dt(FROZEN - 2 * step)})") is False


@pytest.mark.parametrize(("label", "step"), TIME_UNITS)
def test_datetime_is_in_previous_n_units(
    frozen_clock: pydt.datetime, label: str, step: pydt.timedelta
) -> None:
    call = f"DateTime.IsInPreviousN{label}s"
    assert evaluate(f"{call}({_dt(FROZEN - 2 * step)}, 2)") is True
    assert evaluate(f"{call}({_dt(FROZEN - 3 * step)}, 2)") is False
    assert evaluate(f"{call}({_dt(FROZEN)}, 2)") is False


def test_hour_predicates_cross_a_day_boundary(frozen_clock: pydt.datetime) -> None:
    # An hour offset is not "same date, hour + 1" - 23:00 today and 00:00
    # tomorrow are adjacent hours.
    late = pydt.datetime(2026, 5, 14, 23, 30)
    early_next_day = pydt.datetime(2026, 5, 15, 0, 30)
    temporal_now = late
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(temporal, "_now_local", lambda: temporal_now)
        assert evaluate(f"DateTime.IsInNextHour({_dt(early_next_day)})") is True
        assert evaluate(f"DateTime.IsInPreviousHour({_dt(early_next_day)})") is False


# --------------------------------------------------------------------------
# DateTime.FromFileTime / DateTime.ToRecord
# --------------------------------------------------------------------------

# Ticks between the FILETIME epoch (1601-01-01) and the Unix epoch. Derived,
# not recalled: computed from Python's own calendar below.
_FILETIME_TO_UNIX_TICKS = (
    pydt.datetime(1970, 1, 1) - pydt.datetime(1601, 1, 1)
).total_seconds() * 10**7


def test_filetime_epoch_gap_is_the_documented_constant() -> None:
    assert _FILETIME_TO_UNIX_TICKS == 116444736000000000


@pytest.mark.parametrize("ticks", [129876402529842245, 116444736000000000, 0])
def test_datetime_from_file_time_matches_pythons_own_local_conversion(
    ticks: int,
) -> None:
    # Host-dependent by specification ("converts it to the local time zone"),
    # so this compares against Python's own local conversion of the SAME
    # instant on the SAME host rather than a hard-coded wall clock.
    unix_seconds = (ticks - _FILETIME_TO_UNIX_TICKS) / 10**7
    expected = pydt.datetime.fromtimestamp(unix_seconds)
    result = evaluate(f"DateTime.FromFileTime({ticks})")
    assert result.tzinfo is None
    assert abs(result - expected) < pydt.timedelta(microseconds=2)


def test_datetime_from_file_time_docs_example_is_the_right_instant() -> None:
    # docs: DateTime.FromFileTime(129876402529842245)
    #       -> #datetime(2012,7,24,14,50,52.9842245) on a UTC-7 machine, i.e.
    # 21:50:52.9842245 UTC. That UTC instant is host-independent, so it is
    # what gets asserted.
    result = evaluate("DateTimeZone.FromFileTime(129876402529842245)")
    assert result.astimezone(pydt.UTC).replace(tzinfo=None) == pydt.datetime(
        2012, 7, 24, 21, 50, 52, 984224
    )


def test_datetimezone_from_file_time_carries_the_local_offset() -> None:
    result = evaluate("DateTimeZone.FromFileTime(129876402529842245)")
    assert result.tzinfo is not None
    assert result.utcoffset() == result.astimezone().utcoffset()


def test_from_file_time_propagates_null_and_rejects_text() -> None:
    assert evaluate("DateTime.FromFileTime(null)") is None
    assert evaluate("DateTimeZone.FromFileTime(null)") is None
    with pytest.raises(EvalError):
        evaluate('DateTime.FromFileTime("129876402529842245")')


def test_datetime_to_record_matches_the_docs_example() -> None:
    # docs: DateTime.ToRecord(#datetime(2011,12,31,11,56,2))
    #       -> [Year=2011, Month=12, Day=31, Hour=11, Minute=56, Second=2]
    assert evaluate("DateTime.ToRecord(#datetime(2011,12,31,11,56,2))") == {
        "Year": 2011,
        "Month": 12,
        "Day": 31,
        "Hour": 11,
        "Minute": 56,
        "Second": 2,
    }


def test_to_record_folds_a_fractional_second_into_the_second_field() -> None:
    # CHOICE: no docs example shows a fractional second, and no field name
    # for one is documented. Rather than invent a "Millisecond" field or drop
    # the fraction, it rides in Second as a float; a whole second stays an
    # int so the documented examples match exactly.
    assert evaluate("DateTime.ToRecord(#datetime(2011,12,31,11,56,2.5))") == {
        "Year": 2011,
        "Month": 12,
        "Day": 31,
        "Hour": 11,
        "Minute": 56,
        "Second": 2.5,
    }


# --------------------------------------------------------------------------
# DateTimeZone.From / FromText
# --------------------------------------------------------------------------


def test_datetimezone_from_text_matches_the_docs_example() -> None:
    # docs: DateTimeZone.FromText("2010-12-31T01:30:00-08:00")
    #       -> #datetimezone(2010,12,31,1,30,0,-8,0)
    result = evaluate('DateTimeZone.FromText("2010-12-31T01:30:00-08:00")')
    assert result == pydt.datetime(
        2010, 12, 31, 1, 30, tzinfo=pydt.timezone(pydt.timedelta(hours=-8))
    )


def test_datetimezone_from_text_accepts_a_z_suffix() -> None:
    assert evaluate('DateTimeZone.FromText("2024-03-09T08:15:00Z")') == pydt.datetime(
        2024, 3, 9, 8, 15, tzinfo=pydt.UTC
    )


def test_datetimezone_from_text_refuses_text_with_no_offset() -> None:
    # A datetimezone without a zone would have to invent one.
    with pytest.raises(EvalError, match="no time zone offset"):
        evaluate('DateTimeZone.FromText("2024-03-09T08:15:00")')


def test_datetimezone_from_text_rejects_unparseable_text() -> None:
    with pytest.raises(EvalError, match="not a valid ISO datetimezone"):
        evaluate('DateTimeZone.FromText("not a timestamp")')


def test_datetimezone_from_text_rejects_a_non_text_argument() -> None:
    with pytest.raises(EvalError, match="expected text"):
        evaluate("DateTimeZone.FromText(42)")


def test_datetimezone_from_text_propagates_null() -> None:
    assert evaluate("DateTimeZone.FromText(null)") is None


def test_datetimezone_from_text_accepts_a_legacy_culture_string() -> None:
    assert evaluate(
        'DateTimeZone.FromText("2010-12-31T01:30:00-08:00", "en-US")'
    ) == pydt.datetime(
        2010, 12, 31, 1, 30, tzinfo=pydt.timezone(pydt.timedelta(hours=-8))
    )


def test_datetimezone_from_text_refuses_a_non_invariant_culture() -> None:
    with pytest.raises(UnsupportedError, match="de-DE"):
        evaluate('DateTimeZone.FromText("30.12.2010 02:04:50 +02:00", "de-DE")')


def test_datetimezone_from_text_refuses_a_format_string() -> None:
    # REFUSAL: parsing by a custom/standard format string is the reverse of
    # `_format_custom` and is not implemented. Silently ignoring the option
    # would parse text the caller never asked to be parsed that way.
    with pytest.raises(UnsupportedError, match="Format"):
        evaluate(
            'DateTimeZone.FromText("2009-06-15T13:45:30.0000000-07:00", '
            '[Format="O", Culture="en-US"])'
        )


def test_datetimezone_from_text_refuses_an_unknown_option() -> None:
    with pytest.raises(UnsupportedError, match="Nonsense"):
        evaluate('DateTimeZone.FromText("2024-03-09T08:15:00Z", [Nonsense=1])')


def test_datetimezone_from_passes_a_datetimezone_through() -> None:
    source = "#datetimezone(2026,1,9,13,45,0,5,30)"
    assert evaluate(f"DateTimeZone.From({source})") == evaluate(source)


def test_datetimezone_from_text_delegates() -> None:
    assert evaluate('DateTimeZone.From("2020-10-30T01:30:00-08:00")') == pydt.datetime(
        2020, 10, 30, 1, 30, tzinfo=pydt.timezone(pydt.timedelta(hours=-8))
    )


def test_datetimezone_from_a_date_is_midnight_in_the_local_zone() -> None:
    # docs: "date: returns a datetimezone with value as the date component,
    # 12:00:00 AM as the time component, and the offset corresponding [to]
    # the local time zone." Host-dependent by specification, so the expected
    # offset is Python's own answer for that same local instant.
    result = evaluate("DateTimeZone.From(#date(2020,1,1))")
    expected = pydt.datetime(2020, 1, 1).astimezone()
    assert result == expected
    assert result.hour == 0 and result.minute == 0


def test_datetimezone_from_a_naive_datetime_keeps_the_wall_clock() -> None:
    result = evaluate("DateTimeZone.From(#datetime(2020,6,15,9,30,0))")
    assert result.replace(tzinfo=None) == pydt.datetime(2020, 6, 15, 9, 30)
    assert (
        result.utcoffset() == pydt.datetime(2020, 6, 15, 9, 30).astimezone().utcoffset()
    )


def test_datetimezone_from_a_time_uses_the_ole_epoch_date() -> None:
    # docs spell this out: "the date equivalent of the OLE Automation Date of
    # 0 as the date component" - 30 December 1899.
    result = evaluate("DateTimeZone.From(#time(6,15,0))")
    assert result.date() == pydt.date(1899, 12, 30)
    assert (result.hour, result.minute) == (6, 15)
    assert result.tzinfo is not None


def test_datetimezone_from_a_number_is_an_ole_serial() -> None:
    # Cross-checked against this module's documented OLE epoch: serial 45000
    # is 1899-12-30 + 45000 days.
    expected_date = pydt.date(1899, 12, 30) + pydt.timedelta(days=45000)
    result = evaluate("DateTimeZone.From(45000.5)")
    assert result.date() == expected_date
    assert (result.hour, result.minute) == (12, 0)


def test_datetimezone_from_null_and_bad_types() -> None:
    assert evaluate("DateTimeZone.From(null)") is None
    with pytest.raises(EvalError, match="logical"):
        evaluate("DateTimeZone.From(true)")
    with pytest.raises(EvalError, match="list"):
        evaluate("DateTimeZone.From({1,2})")


def test_datetimezone_from_refuses_a_non_invariant_culture() -> None:
    with pytest.raises(UnsupportedError, match="fr-FR"):
        evaluate('DateTimeZone.From("2020-10-30T01:30:00-08:00", "fr-FR")')


# --------------------------------------------------------------------------
# DateTimeZone clock readers - never asserted on an exact instant
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call", ["DateTimeZone.LocalNow()", "DateTimeZone.FixedLocalNow()"]
)
def test_local_now_family_returns_an_aware_value_near_now(call: str) -> None:
    before = pydt.datetime.now(pydt.UTC)
    result = evaluate(call)
    after = pydt.datetime.now(pydt.UTC)
    assert isinstance(result, pydt.datetime)
    assert result.tzinfo is not None
    assert before <= result <= after
    assert result.utcoffset() == pydt.datetime.now().astimezone().utcoffset()


@pytest.mark.parametrize(
    "call", ["DateTimeZone.UtcNow()", "DateTimeZone.FixedUtcNow()"]
)
def test_utc_now_family_returns_a_zero_offset_value_near_now(call: str) -> None:
    before = pydt.datetime.now(pydt.UTC)
    result = evaluate(call)
    after = pydt.datetime.now(pydt.UTC)
    assert result.utcoffset() == pydt.timedelta(0)
    assert before <= result <= after


@pytest.mark.parametrize(
    "call",
    [
        "DateTimeZone.LocalNow(1)",
        "DateTimeZone.FixedLocalNow(1)",
        "DateTimeZone.UtcNow(1)",
        "DateTimeZone.FixedUtcNow(1)",
    ],
)
def test_clock_readers_take_no_arguments(call: str) -> None:
    with pytest.raises(UnsupportedError):
        evaluate(call)


# --------------------------------------------------------------------------
# DateTimeZone zone surgery
# --------------------------------------------------------------------------


def test_remove_zone_matches_the_docs_example() -> None:
    # docs: DateTimeZone.RemoveZone(#datetimezone(2011,12,31,9,15,36,-7,0))
    #       -> #datetime(2011,12,31,9,15,36)
    result = evaluate("DateTimeZone.RemoveZone(#datetimezone(2011,12,31,9,15,36,-7,0))")
    assert result == pydt.datetime(2011, 12, 31, 9, 15, 36)
    assert result.tzinfo is None


def test_remove_zone_refuses_a_value_that_has_no_zone() -> None:
    # CHOICE: the docs declare the parameter `nullable datetimezone` and say
    # nothing about a zone-less argument. Refusing mirrors DateTime.AddZone's
    # existing "value already has a time zone" guard from the other side.
    with pytest.raises(EvalError, match="no zone"):
        evaluate("DateTimeZone.RemoveZone(#datetime(2011,12,31,9,15,36))")


def test_switch_zone_matches_both_docs_examples() -> None:
    # docs 1: SwitchZone(#datetimezone(2010,12,31,11,56,2,7,30), 8)
    #         -> #datetimezone(2010,12,31,12,26,2,8,0)
    first = evaluate(
        "DateTimeZone.SwitchZone(#datetimezone(2010,12,31,11,56,2,7,30), 8)"
    )
    assert first.replace(tzinfo=None) == pydt.datetime(2010, 12, 31, 12, 26, 2)
    assert first.utcoffset() == pydt.timedelta(hours=8)
    # docs 2: SwitchZone(..., 0, -30) -> #datetimezone(2010,12,31,3,56,2,0,-30)
    second = evaluate(
        "DateTimeZone.SwitchZone(#datetimezone(2010,12,31,11,56,2,7,30), 0, -30)"
    )
    assert second.replace(tzinfo=None) == pydt.datetime(2010, 12, 31, 3, 56, 2)
    assert second.utcoffset() == pydt.timedelta(minutes=-30)


def test_switch_zone_keeps_the_same_instant() -> None:
    source = pydt.datetime(
        2026, 1, 9, 13, 45, tzinfo=pydt.timezone(pydt.timedelta(hours=5, minutes=30))
    )
    result = evaluate(f"DateTimeZone.SwitchZone({_dtz(source)}, -3)")
    assert result == source  # same instant, different wall clock
    assert result.replace(tzinfo=None) == source.astimezone(
        pydt.timezone(pydt.timedelta(hours=-3))
    ).replace(tzinfo=None)


def test_switch_zone_refuses_a_value_that_has_no_zone() -> None:
    # docs: "If dateTimeZone does not have a timezone component, an error is
    # raised."
    with pytest.raises(EvalError, match="no zone"):
        evaluate("DateTimeZone.SwitchZone(#datetime(2010,12,31,11,56,2), 8)")


def test_switch_zone_propagates_null() -> None:
    assert evaluate("DateTimeZone.SwitchZone(null, 8)") is None


def test_to_utc_matches_the_docs_example() -> None:
    # docs: ToUtc(#datetimezone(2010,12,31,11,56,2,7,30))
    #       -> #datetimezone(2010,12,31,4,26,2,0,0)
    result = evaluate("DateTimeZone.ToUtc(#datetimezone(2010,12,31,11,56,2,7,30))")
    assert result.replace(tzinfo=None) == pydt.datetime(2010, 12, 31, 4, 26, 2)
    assert result.utcoffset() == pydt.timedelta(0)


def test_to_utc_labels_a_zone_less_value_without_shifting_it() -> None:
    # docs: "If dateTimeZone does not have a timezone component, the UTC
    # timezone information is ADDED" - added, not converted, so the wall
    # clock must not move (the same rule DateTime.AddZone follows).
    result = evaluate("DateTimeZone.ToUtc(#datetime(2010,12,31,11,56,2))")
    assert result.replace(tzinfo=None) == pydt.datetime(2010, 12, 31, 11, 56, 2)
    assert result.utcoffset() == pydt.timedelta(0)


def test_to_local_converts_an_aware_value_to_the_host_offset() -> None:
    source = pydt.datetime(
        2026, 1, 9, 13, 45, tzinfo=pydt.timezone(pydt.timedelta(hours=5, minutes=30))
    )
    result = evaluate(f"DateTimeZone.ToLocal({_dtz(source)})")
    assert result == source  # same instant
    assert result.utcoffset() == source.astimezone().utcoffset()


def test_to_local_labels_a_zone_less_value_without_shifting_it() -> None:
    # docs: "If dateTimeZone does not have a timezone component, the local
    # timezone information is added." A naive value is already local wall
    # clock, so only the label changes.
    naive = pydt.datetime(2026, 1, 9, 13, 45)
    result = evaluate(f"DateTimeZone.ToLocal({_dt(naive)})")
    assert result.replace(tzinfo=None) == naive
    assert result.utcoffset() == naive.astimezone().utcoffset()


def test_to_local_and_to_utc_propagate_null_and_reject_other_types() -> None:
    assert evaluate("DateTimeZone.ToLocal(null)") is None
    assert evaluate("DateTimeZone.ToUtc(null)") is None
    with pytest.raises(EvalError, match="expected a datetimezone"):
        evaluate('DateTimeZone.ToUtc("2020-01-01T00:00:00Z")')


# --------------------------------------------------------------------------
# DateTimeZone.ZoneHours / ZoneMinutes / ToRecord / ToText
# --------------------------------------------------------------------------


def test_zone_hours_and_minutes_match_the_docs_example() -> None:
    # docs: ZoneHours(#datetimezone(2024,4,28,13,24,22,7,30)) -> 7
    #       ZoneMinutes(same) -> 30
    source = "#datetimezone(2024,4,28,13,24,22,7,30)"
    assert evaluate(f"DateTimeZone.ZoneHours({source})") == 7
    assert evaluate(f"DateTimeZone.ZoneMinutes({source})") == 30


def test_zone_components_round_trip_a_negative_offset() -> None:
    # CHOICE: no docs example has a negative offset with a non-zero hour
    # part, so the sign convention (both components carry the sign, matching
    # SwitchZone's own `(0, -30)` shape) is pinned by round-tripping the
    # #datetimezone literal instead.
    source = "#datetimezone(2024,4,28,13,24,22,-7,-30)"
    assert evaluate(f"DateTimeZone.ZoneHours({source})") == -7
    assert evaluate(f"DateTimeZone.ZoneMinutes({source})") == -30
    assert evaluate(source).utcoffset() == pydt.timedelta(hours=-7, minutes=-30)


def test_zone_components_refuse_a_zone_less_value_and_propagate_null() -> None:
    assert evaluate("DateTimeZone.ZoneHours(null)") is None
    assert evaluate("DateTimeZone.ZoneMinutes(null)") is None
    with pytest.raises(EvalError, match="no zone"):
        evaluate("DateTimeZone.ZoneHours(#datetime(2024,4,28,13,24,22))")
    with pytest.raises(EvalError, match="no zone"):
        evaluate("DateTimeZone.ZoneMinutes(#datetime(2024,4,28,13,24,22))")


def test_datetimezone_to_record_matches_the_docs_example() -> None:
    # docs: ToRecord(#datetimezone(2011,12,31,11,56,2,8,0)) -> [Year=2011,
    # Month=12, Day=31, Hour=11, Minute=56, Second=2, ZoneHours=8,
    # ZoneMinutes=0]
    assert evaluate("DateTimeZone.ToRecord(#datetimezone(2011,12,31,11,56,2,8,0))") == {
        "Year": 2011,
        "Month": 12,
        "Day": 31,
        "Hour": 11,
        "Minute": 56,
        "Second": 2,
        "ZoneHours": 8,
        "ZoneMinutes": 0,
    }


def test_datetimezone_to_record_refuses_a_zone_less_value() -> None:
    with pytest.raises(EvalError, match="no zone"):
        evaluate("DateTimeZone.ToRecord(#datetime(2011,12,31,11,56,2))")


def test_datetimezone_to_text_round_trip_format_matches_the_docs_example() -> None:
    # docs: ToText(#datetimezone(2000,2,8,3,45,12,2,0), [Format="O",
    # Culture="en-US"]) -> "2000-02-08T03:45:12.0000000+02:00"
    assert (
        evaluate(
            "DateTimeZone.ToText(#datetimezone(2000,2,8,3,45,12,2,0), "
            '[Format="O", Culture="en-US"])'
        )
        == "2000-02-08T03:45:12.0000000+02:00"
    )


def test_datetimezone_to_text_default_is_the_invariant_iso_shape() -> None:
    # CHOICE: real Power Query's culture-formatted default for this function
    # is "12/31/2010 1:30:25 AM +02:00". This module's other three ToText
    # functions already default to an unambiguous ISO rendering instead, and
    # this one stays consistent with them.
    assert (
        evaluate("DateTimeZone.ToText(#datetimezone(2010,12,31,1,30,25,2,0))")
        == "2010-12-31 01:30:25+02:00"
    )


def test_datetimezone_to_text_renders_a_custom_zone_specifier() -> None:
    assert (
        evaluate(
            "DateTimeZone.ToText(#datetimezone(2010,12,30,2,4,50,-8,0), "
            '"dd MMM yyyy HH:mm:ss zzz")'
        )
        == "30 Dec 2010 02:04:50 -08:00"
    )


def test_datetimezone_to_text_refuses_a_non_invariant_culture() -> None:
    with pytest.raises(UnsupportedError, match="de-DE"):
        evaluate(
            "DateTimeZone.ToText(#datetimezone(2010,12,30,2,4,50,-8,0), "
            '[Format="dd MMM yyyy", Culture="de-DE"])'
        )


def test_datetimezone_to_text_refuses_a_zone_less_value_and_propagates_null() -> None:
    assert evaluate("DateTimeZone.ToText(null)") is None
    with pytest.raises(EvalError, match="no zone"):
        evaluate("DateTimeZone.ToText(#datetime(2010,12,31,1,30,25))")


# --------------------------------------------------------------------------
# Time.StartOfHour / EndOfHour / ToRecord
# --------------------------------------------------------------------------


def test_time_start_of_hour_matches_the_docs_example() -> None:
    # docs: Time.StartOfHour(#datetime(2011,10,10,8,10,32))
    #       -> #datetime(2011,10,10,8,0,0)
    assert evaluate("Time.StartOfHour(#datetime(2011,10,10,8,10,32))") == pydt.datetime(
        2011, 10, 10, 8, 0, 0
    )


def test_time_end_of_hour_matches_both_docs_examples() -> None:
    # docs 1: Time.EndOfHour(#datetime(2011,5,14,17,0,0))
    #         -> #datetime(2011,5,14,17,59,59.9999999)
    assert evaluate("Time.EndOfHour(#datetime(2011,5,14,17,0,0))") == pydt.datetime(
        2011, 5, 14, 17, 59, 59, 999_999
    )
    # docs 2: the zone survives.
    zoned = evaluate("Time.EndOfHour(#datetimezone(2011,5,17,5,0,0,-7,0))")
    assert zoned.utcoffset() == pydt.timedelta(hours=-7)
    assert zoned.replace(tzinfo=None) == pydt.datetime(2011, 5, 17, 5, 59, 59, 999_999)


def test_time_hour_boundaries_of_a_bare_time_stay_a_time() -> None:
    start = evaluate("Time.StartOfHour(#time(8,10,32))")
    end = evaluate("Time.EndOfHour(#time(8,10,32))")
    assert start == pydt.time(8, 0, 0)
    assert end == pydt.time(8, 59, 59, 999_999)
    assert type(start) is pydt.time


def test_time_hour_boundaries_propagate_null() -> None:
    assert evaluate("Time.StartOfHour(null)") is None
    assert evaluate("Time.EndOfHour(null)") is None


def test_time_to_record_matches_the_docs_example() -> None:
    # docs: Time.ToRecord(#time(11,56,2)) -> [Hour=11, Minute=56, Second=2]
    assert evaluate("Time.ToRecord(#time(11,56,2))") == {
        "Hour": 11,
        "Minute": 56,
        "Second": 2,
    }


def test_time_to_record_keeps_a_fractional_second() -> None:
    assert evaluate("Time.ToRecord(#time(11,56,2.25))") == {
        "Hour": 11,
        "Minute": 56,
        "Second": 2.25,
    }


# --------------------------------------------------------------------------
# Duration.ToRecord
# --------------------------------------------------------------------------


def test_duration_to_record_matches_the_docs_example() -> None:
    # docs: Duration.ToRecord(#duration(2,5,55,20))
    #       -> [Days=2, Hours=5, Minutes=55, Seconds=20]
    assert evaluate("Duration.ToRecord(#duration(2,5,55,20))") == {
        "Days": 2,
        "Hours": 5,
        "Minutes": 55,
        "Seconds": 20,
    }


def test_duration_to_record_agrees_with_the_component_accessors() -> None:
    source = "#duration(2,5,55,20)"
    record = evaluate(f"Duration.ToRecord({source})")
    assert record["Days"] == evaluate(f"Duration.Days({source})")
    assert record["Hours"] == evaluate(f"Duration.Hours({source})")
    assert record["Minutes"] == evaluate(f"Duration.Minutes({source})")
    assert record["Seconds"] == evaluate(f"Duration.Seconds({source})")


def test_duration_to_record_signs_every_component_of_a_negative_duration() -> None:
    # Matches the existing Duration.Days/Hours/Minutes/Seconds convention in
    # this module, which each apply the whole duration's sign to their own
    # component.
    assert evaluate("Duration.ToRecord(#duration(-2,-5,-55,-20))") == {
        "Days": -2,
        "Hours": -5,
        "Minutes": -55,
        "Seconds": -20,
    }


def test_duration_to_record_keeps_a_fractional_second() -> None:
    assert evaluate("Duration.ToRecord(#duration(0,0,0,1.5))")["Seconds"] == 1.5


def test_duration_to_record_propagates_null() -> None:
    assert evaluate("Duration.ToRecord(null)") is None


# --------------------------------------------------------------------------
# Null propagation and arity, across the whole new surface
# --------------------------------------------------------------------------

ONE_ARG_NULL_SAFE = [
    "Date.StartOfDay",
    "Date.EndOfDay",
    "Date.StartOfQuarter",
    "Date.EndOfQuarter",
    "Date.DaysInMonth",
    "Date.IsLeapYear",
    "Date.ToRecord",
    "Date.WeekOfMonth",
    "Date.IsInYearToDate",
    "Date.IsInCurrentDay",
    "Date.IsInCurrentWeek",
    "Date.IsInCurrentQuarter",
    "Date.IsInNextDay",
    "Date.IsInNextWeek",
    "Date.IsInNextMonth",
    "Date.IsInNextQuarter",
    "Date.IsInNextYear",
    "Date.IsInPreviousDay",
    "Date.IsInPreviousWeek",
    "Date.IsInPreviousMonth",
    "Date.IsInPreviousQuarter",
    "Date.IsInPreviousYear",
    "DateTime.IsInCurrentHour",
    "DateTime.IsInCurrentMinute",
    "DateTime.IsInCurrentSecond",
    "DateTime.IsInNextHour",
    "DateTime.IsInNextMinute",
    "DateTime.IsInNextSecond",
    "DateTime.IsInPreviousHour",
    "DateTime.IsInPreviousMinute",
    "DateTime.IsInPreviousSecond",
    "DateTime.ToRecord",
    "DateTimeZone.RemoveZone",
    "DateTimeZone.ToRecord",
    "DateTimeZone.ToLocal",
    "DateTimeZone.ToUtc",
    "DateTimeZone.ZoneHours",
    "DateTimeZone.ZoneMinutes",
    "Time.StartOfHour",
    "Time.EndOfHour",
    "Time.ToRecord",
    "Duration.ToRecord",
]

TWO_ARG_NULL_SAFE = [
    "Date.AddQuarters",
    "Date.IsInNextNDays",
    "Date.IsInNextNWeeks",
    "Date.IsInNextNMonths",
    "Date.IsInNextNQuarters",
    "Date.IsInNextNYears",
    "Date.IsInPreviousNDays",
    "Date.IsInPreviousNWeeks",
    "Date.IsInPreviousNMonths",
    "Date.IsInPreviousNQuarters",
    "Date.IsInPreviousNYears",
    "DateTime.IsInNextNHours",
    "DateTime.IsInNextNMinutes",
    "DateTime.IsInNextNSeconds",
    "DateTime.IsInPreviousNHours",
    "DateTime.IsInPreviousNMinutes",
    "DateTime.IsInPreviousNSeconds",
]


@pytest.mark.parametrize("name", ONE_ARG_NULL_SAFE)
def test_one_argument_functions_propagate_null(name: str) -> None:
    assert evaluate(f"{name}(null)") is None


@pytest.mark.parametrize("name", TWO_ARG_NULL_SAFE)
def test_two_argument_functions_propagate_null(name: str) -> None:
    assert evaluate(f"{name}(null, 1)") is None


@pytest.mark.parametrize("name", ONE_ARG_NULL_SAFE)
def test_one_argument_functions_reject_a_second_argument(name: str) -> None:
    with pytest.raises(UnsupportedError):
        evaluate(f"{name}(null, null, null)")


@pytest.mark.parametrize("name", TWO_ARG_NULL_SAFE)
def test_two_argument_functions_require_their_count(name: str) -> None:
    with pytest.raises(UnsupportedError):
        evaluate(f"{name}(null)")
