"""Tests for `src/pqtools/builtins/_datetime.py` - literals, Date.*,
DateTime.*, Duration.*, Time.*.

Every function gets at least one test; every trap named in the task brief
(Date.DayOfWeek's Sunday=0 default, culture refusal, non-deterministic
now(), the OLE serial numeric coercion, null propagation) gets an explicit
pinning test, not just incidental coverage.

Expected weekday/day-of-year facts are derived from Python's own
`datetime.date` inside each test (never hand-typed from memory) so a test
failure can never be "the test's fact was wrong," only "the implementation
disagrees with Python's own calendar."
"""

from __future__ import annotations

import datetime as pydt

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Literals
# --------------------------------------------------------------------------


def test_date_literal():
    result = evaluate("#date(2024,1,31)")
    assert result == pydt.date(2024, 1, 31)
    assert type(result) is pydt.date


def test_datetime_literal_with_fractional_seconds():
    result = evaluate("#datetime(2024,1,31,10,30,15.5)")
    assert result == pydt.datetime(2024, 1, 31, 10, 30, 15, 500_000)


def test_datetimezone_literal():
    result = evaluate("#datetimezone(2024,1,31,10,30,15,5,30)")
    assert result.tzinfo is not None
    assert result.utcoffset() == pydt.timedelta(hours=5, minutes=30)
    assert (result.year, result.month, result.day) == (2024, 1, 31)
    assert (result.hour, result.minute, result.second) == (10, 30, 15)


def test_time_literal():
    assert evaluate("#time(10,30,15)") == pydt.time(10, 30, 15)


def test_duration_literal():
    assert evaluate("#duration(1,2,3,4)") == pydt.timedelta(
        days=1, hours=2, minutes=3, seconds=4
    )


def test_date_literal_invalid_component_is_eval_error():
    with pytest.raises(EvalError, match="#date"):
        evaluate("#date(2024,13,1)")


def test_date_literal_wrong_arity_is_unsupported():
    with pytest.raises(UnsupportedError):
        evaluate("#date(2024,1)")


def test_literal_rejects_non_number_argument():
    with pytest.raises(EvalError):
        evaluate('#date("2024",1,1)')


# --------------------------------------------------------------------------
# Day.* enum - trap #1 groundwork
# --------------------------------------------------------------------------


def test_day_enum_values():
    assert evaluate("Day.Sunday") == 0
    assert evaluate("Day.Monday") == 1
    assert evaluate("Day.Tuesday") == 2
    assert evaluate("Day.Wednesday") == 3
    assert evaluate("Day.Thursday") == 4
    assert evaluate("Day.Friday") == 5
    assert evaluate("Day.Saturday") == 6


# --------------------------------------------------------------------------
# Date.From
# --------------------------------------------------------------------------


def test_date_from_text_iso():
    assert evaluate('Date.From("2024-01-31")') == pydt.date(2024, 1, 31)


def test_date_from_date_passthrough():
    assert evaluate("Date.From(#date(2024,1,31))") == pydt.date(2024, 1, 31)


def test_date_from_datetime_truncates():
    assert evaluate("Date.From(#datetime(2024,1,31,10,0,0))") == pydt.date(2024, 1, 31)


def test_date_from_ole_serial_number():
    # Independently verified: 1899-12-30 + 45000 days = 2023-03-15.
    assert pydt.date(1899, 12, 30) + pydt.timedelta(days=45000) == pydt.date(
        2023, 3, 15
    )
    assert evaluate("Date.From(45000)") == pydt.date(2023, 3, 15)


def test_date_from_ole_serial_phantom_window_is_unsupported():
    # Trap #5: serials 1-60 fall in the historical Excel/OLE "phantom
    # 1900-02-29" compatibility window - refuse rather than guess.
    with pytest.raises(UnsupportedError, match="phantom"):
        evaluate("Date.From(30)")


def test_date_from_null_propagates():
    assert evaluate("Date.From(null)") is None


def test_date_from_logical_is_eval_error():
    with pytest.raises(EvalError):
        evaluate("Date.From(true)")


def test_date_from_honors_en_us_culture():
    assert evaluate('Date.From("2024-01-31", "en-US")') == pydt.date(2024, 1, 31)


def test_date_from_rejects_other_culture():
    with pytest.raises(UnsupportedError, match="fr-FR"):
        evaluate('Date.From("2024-01-31", "fr-FR")')


# --------------------------------------------------------------------------
# Date.Year / Month / Day / DayOfYear / QuarterOfYear
# --------------------------------------------------------------------------


def test_date_year_month_day():
    assert evaluate("Date.Year(#date(2024,7,9))") == 2024
    assert evaluate("Date.Month(#date(2024,7,9))") == 7
    assert evaluate("Date.Day(#date(2024,7,9))") == 9


def test_date_year_month_day_accept_datetime():
    assert evaluate("Date.Year(#datetime(2024,7,9,1,2,3))") == 2024


def test_date_year_null_propagates():
    assert evaluate("Date.Year(null)") is None


def test_date_day_of_year():
    d = pydt.date(2024, 3, 1)
    expected = (d - pydt.date(2024, 1, 1)).days + 1
    assert evaluate("Date.DayOfYear(#date(2024,3,1))") == expected


def test_date_quarter_of_year():
    assert evaluate("Date.QuarterOfYear(#date(2024,1,1))") == 1
    assert evaluate("Date.QuarterOfYear(#date(2024,4,1))") == 2
    assert evaluate("Date.QuarterOfYear(#date(2024,7,9))") == 3
    assert evaluate("Date.QuarterOfYear(#date(2024,12,31))") == 4


# --------------------------------------------------------------------------
# Date.DayOfWeek - trap #1: Sunday=0 default, Day.* first-day argument
# --------------------------------------------------------------------------


def test_date_day_of_week_defaults_to_sunday_zero():
    # Walk a full week and pin the exact Sunday=0..Saturday=6 numbering
    # against Python's own weekday() (Monday=0..Sunday=6), not a hardcoded
    # "January 1 2024 was a Monday" fact.
    start = pydt.date(2024, 1, 7)  # any known Sunday would do; derived below
    # Find the Sunday on/before `start` using Python's own calendar so the
    # test never hand-asserts which weekday a date fell on.
    sunday = start - pydt.timedelta(days=(start.weekday() + 1) % 7)
    assert sunday.weekday() == 6  # Python: Sunday == 6
    for offset in range(7):
        d = sunday + pydt.timedelta(days=offset)
        query = f"Date.DayOfWeek(#date({d.year},{d.month},{d.day}))"
        assert evaluate(query) == offset


def test_date_day_of_week_with_explicit_first_day():
    # Same week, but first-day-of-week = Monday: Monday must come back 0.
    monday = pydt.date(2024, 1, 8)
    assert monday.weekday() == 0  # Python: Monday == 0
    assert (
        evaluate(
            f"Date.DayOfWeek(#date({monday.year},{monday.month},{monday.day}), "
            "Day.Monday)"
        )
        == 0
    )
    sunday = monday + pydt.timedelta(days=6)
    assert (
        evaluate(
            f"Date.DayOfWeek(#date({sunday.year},{sunday.month},{sunday.day}), "
            "Day.Monday)"
        )
        == 6
    )


def test_date_day_of_week_rejects_out_of_range_first_day():
    with pytest.raises(EvalError):
        evaluate("Date.DayOfWeek(#date(2024,1,1), 9)")


def test_date_day_of_week_null_propagates():
    assert evaluate("Date.DayOfWeek(null)") is None


# --------------------------------------------------------------------------
# Date.DayOfWeekName / Date.MonthName - trap #2: culture
# --------------------------------------------------------------------------


def test_date_day_of_week_name_matches_python_calendar():
    for offset in range(7):
        d = pydt.date(2024, 1, 7) + pydt.timedelta(days=offset)  # a full week
        # Sunday-based index, matching Date.DayOfWeek's own numbering.
        sunday_based = (d.weekday() + 1) % 7
        expected = [
            "Sunday",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ][sunday_based]
        query = f"Date.DayOfWeekName(#date({d.year},{d.month},{d.day}))"
        assert evaluate(query) == expected


def test_date_day_of_week_name_rejects_other_culture():
    with pytest.raises(UnsupportedError, match="de-DE"):
        evaluate('Date.DayOfWeekName(#date(2024,1,1), "de-DE")')


def test_date_month_name_all_twelve():
    names = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    for month, name in enumerate(names, start=1):
        assert evaluate(f"Date.MonthName(#date(2024,{month},1))") == name


def test_date_month_name_rejects_other_culture():
    with pytest.raises(UnsupportedError, match="fr-FR"):
        evaluate('Date.MonthName(#date(2024,1,1), "fr-FR")')


def test_date_month_name_honors_en_us_culture():
    assert evaluate('Date.MonthName(#date(2024,7,9), "en-US")') == "July"


# --------------------------------------------------------------------------
# Date.AddDays / AddWeeks / AddMonths / AddYears
# --------------------------------------------------------------------------


def test_date_add_days():
    assert evaluate("Date.AddDays(#date(2024,1,31), 1)") == pydt.date(2024, 2, 1)
    assert evaluate("Date.AddDays(#date(2024,1,31), -31)") == pydt.date(2023, 12, 31)


def test_date_add_weeks():
    assert evaluate("Date.AddWeeks(#date(2024,1,1), 2)") == pydt.date(2024, 1, 15)


def test_date_add_months_clamps_to_shorter_month():
    # Jan 31 + 1 month must clamp into February - leap year gets the 29th,
    # a non-leap year gets the 28th. Pinned per the task's correctness bar
    # ("every function whose semantics you had to reason about gets an
    # edge-case test").
    assert evaluate("Date.AddMonths(#date(2024,1,31), 1)") == pydt.date(2024, 2, 29)
    assert evaluate("Date.AddMonths(#date(2023,1,31), 1)") == pydt.date(2023, 2, 28)


def test_date_add_months_crosses_year_boundary():
    assert evaluate("Date.AddMonths(#date(2024,12,15), 1)") == pydt.date(2025, 1, 15)
    assert evaluate("Date.AddMonths(#date(2024,1,15), -1)") == pydt.date(2023, 12, 15)


def test_date_add_years_leap_day_clamps():
    assert evaluate("Date.AddYears(#date(2024,2,29), 1)") == pydt.date(2025, 2, 28)


def test_date_add_preserves_datetime_shape():
    result = evaluate("Date.AddDays(#datetime(2024,1,31,10,30,0), 1)")
    assert isinstance(result, pydt.datetime)
    assert result == pydt.datetime(2024, 2, 1, 10, 30, 0)


def test_date_add_days_null_propagates():
    assert evaluate("Date.AddDays(null, 1)") is None


# --------------------------------------------------------------------------
# Date.StartOfMonth / EndOfMonth / StartOfYear / EndOfYear
# --------------------------------------------------------------------------


def test_date_start_and_end_of_month():
    assert evaluate("Date.StartOfMonth(#date(2024,2,15))") == pydt.date(2024, 2, 1)
    assert evaluate("Date.EndOfMonth(#date(2024,2,15))") == pydt.date(2024, 2, 29)
    assert evaluate("Date.EndOfMonth(#date(2023,2,15))") == pydt.date(2023, 2, 28)


def test_date_start_and_end_of_year():
    assert evaluate("Date.StartOfYear(#date(2024,7,9))") == pydt.date(2024, 1, 1)
    assert evaluate("Date.EndOfYear(#date(2024,7,9))") == pydt.date(2024, 12, 31)


def test_date_start_of_month_preserves_datetime_shape_at_midnight():
    result = evaluate("Date.StartOfMonth(#datetime(2024,2,15,10,30,0))")
    assert result == pydt.datetime(2024, 2, 1, 0, 0, 0)


# --------------------------------------------------------------------------
# Date.StartOfWeek / EndOfWeek / WeekOfYear
# --------------------------------------------------------------------------


def test_date_start_and_end_of_week_default_sunday():
    wednesday = pydt.date(2024, 1, 10)
    assert wednesday.weekday() == 2  # Python: Wednesday == 2
    start = evaluate(
        f"Date.StartOfWeek(#date({wednesday.year},{wednesday.month},{wednesday.day}))"
    )
    end = evaluate(
        f"Date.EndOfWeek(#date({wednesday.year},{wednesday.month},{wednesday.day}))"
    )
    assert start.weekday() == 6  # the prior/same Sunday
    assert end == start + pydt.timedelta(days=6)
    assert start <= wednesday <= end


def test_date_start_of_week_with_monday_first():
    wednesday = pydt.date(2024, 1, 10)
    start = evaluate(
        f"Date.StartOfWeek(#date({wednesday.year},{wednesday.month},{wednesday.day}), "
        "Day.Monday)"
    )
    assert start.weekday() == 0  # Python: Monday == 0
    assert start <= wednesday <= start + pydt.timedelta(days=6)


def test_date_week_of_year_first_week_is_one():
    assert evaluate("Date.WeekOfYear(#date(2024,1,1))") == 1


def test_date_week_of_year_increments():
    # Two dates a full 7 days apart, both after week 1, must be exactly one
    # week apart in WeekOfYear's own numbering.
    first = evaluate("Date.WeekOfYear(#date(2024,2,4))")
    second = evaluate("Date.WeekOfYear(#date(2024,2,11))")
    assert second == first + 1


# --------------------------------------------------------------------------
# Date.ToText
# --------------------------------------------------------------------------


def test_date_to_text_default_is_iso():
    assert evaluate("Date.ToText(#date(2024,1,31))") == "2024-01-31"


def test_date_to_text_custom_format():
    assert evaluate('Date.ToText(#date(2024,1,31), "yyyy-MM-dd")') == "2024-01-31"
    assert evaluate('Date.ToText(#date(2024,1,31), "MM/dd/yyyy")') == "01/31/2024"
    assert evaluate('Date.ToText(#date(2024,7,9), "MMMM d, yyyy")') == "July 9, 2024"


def test_date_to_text_options_record():
    assert (
        evaluate('Date.ToText(#date(2024,1,31), [Format="yyyy/MM/dd"])') == "2024/01/31"
    )


def test_date_to_text_unknown_option_is_unsupported():
    with pytest.raises(UnsupportedError):
        evaluate('Date.ToText(#date(2024,1,31), [Bogus="x"])')


def test_date_to_text_unknown_format_specifier_is_unsupported():
    with pytest.raises(UnsupportedError, match="QQQ"):
        evaluate('Date.ToText(#date(2024,1,1), "QQQ")')


def test_date_to_text_malformed_run_is_unsupported_not_silently_truncated():
    # "yyy" is not a valid token (only "yyyy"/"yy" are) - it must be
    # rejected as a whole, never silently rendered as "yy" plus a stray "y".
    with pytest.raises(UnsupportedError, match="yyy"):
        evaluate('Date.ToText(#date(2024,1,1), "yyy")')


def test_date_to_text_null_propagates():
    assert evaluate("Date.ToText(null)") is None


# --------------------------------------------------------------------------
# Date.IsInCurrentMonth / IsInCurrentYear - non-deterministic, don't assert
# on a fixed date (analogous to trap #3 for LocalNow)
# --------------------------------------------------------------------------


def test_date_is_in_current_month_and_year():
    today = pydt.date.today()
    query = f"Date.IsInCurrentMonth(#date({today.year},{today.month},{today.day}))"
    assert evaluate(query) is True
    assert evaluate("Date.IsInCurrentMonth(#date(1999,1,1))") is False

    query = f"Date.IsInCurrentYear(#date({today.year},1,1))"
    assert evaluate(query) is True
    assert evaluate("Date.IsInCurrentYear(#date(1999,1,1))") is False


# --------------------------------------------------------------------------
# DateTime.From / Date / Time
# --------------------------------------------------------------------------


def test_datetime_from_text_and_passthrough():
    assert evaluate('DateTime.From("2024-01-31T10:30:15")') == pydt.datetime(
        2024, 1, 31, 10, 30, 15
    )
    assert evaluate("DateTime.From(#date(2024,1,31))") == pydt.datetime(2024, 1, 31)


def test_datetime_from_null_propagates():
    assert evaluate("DateTime.From(null)") is None


def test_datetime_date_and_time_split():
    assert evaluate("DateTime.Date(#datetime(2024,1,31,10,30,15))") == pydt.date(
        2024, 1, 31
    )
    assert evaluate("DateTime.Time(#datetime(2024,1,31,10,30,15))") == pydt.time(
        10, 30, 15
    )


# --------------------------------------------------------------------------
# DateTime.LocalNow / FixedLocalNow - trap #3: implement, never assert on
# the exact wall-clock value
# --------------------------------------------------------------------------


def test_datetime_local_now_is_close_to_now():
    before = pydt.datetime.now()
    result = evaluate("DateTime.LocalNow()")
    after = pydt.datetime.now()
    assert isinstance(result, pydt.datetime)
    assert (
        before - pydt.timedelta(seconds=5)
        <= result
        <= after + pydt.timedelta(seconds=5)
    )


def test_datetime_fixed_local_now_is_close_to_now():
    before = pydt.datetime.now()
    result = evaluate("DateTime.FixedLocalNow()")
    after = pydt.datetime.now()
    assert isinstance(result, pydt.datetime)
    assert (
        before - pydt.timedelta(seconds=5)
        <= result
        <= after + pydt.timedelta(seconds=5)
    )


# --------------------------------------------------------------------------
# DateTime.ToText / AddZone
# --------------------------------------------------------------------------


def test_datetime_to_text_default_and_custom():
    assert (
        evaluate("DateTime.ToText(#datetime(2024,1,31,10,30,15))")
        == "2024-01-31 10:30:15"
    )
    assert (
        evaluate('DateTime.ToText(#datetime(2024,1,31,14,5,3), "yyyy-MM-dd HH:mm:ss")')
        == "2024-01-31 14:05:03"
    )
    assert (
        evaluate('DateTime.ToText(#datetime(2024,1,31,14,5,3), "hh:mm:ss tt")')
        == "02:05:03 PM"
    )


def test_datetime_add_zone_attaches_without_shifting_wall_clock():
    result = evaluate("DateTime.AddZone(#datetime(2024,1,31,10,30,15), 5, 30)")
    assert (result.hour, result.minute, result.second) == (10, 30, 15)
    assert result.utcoffset() == pydt.timedelta(hours=5, minutes=30)


def test_datetime_add_zone_rejects_already_aware_value():
    with pytest.raises(EvalError):
        evaluate("DateTime.AddZone(#datetimezone(2024,1,31,10,30,15,5,30), 1, 0)")


def test_datetime_add_zone_default_zero_minutes():
    result = evaluate("DateTime.AddZone(#datetime(2024,1,31,10,30,15), -8)")
    assert result.utcoffset() == pydt.timedelta(hours=-8)


# --------------------------------------------------------------------------
# Duration.*
# --------------------------------------------------------------------------


def test_duration_from_number_is_total_days():
    assert evaluate("Duration.From(1.5)") == pydt.timedelta(days=1, hours=12)


def test_duration_from_null_propagates():
    assert evaluate("Duration.From(null)") is None


def test_duration_components_positive():
    assert evaluate("Duration.Days(#duration(1,2,3,4))") == 1
    assert evaluate("Duration.Hours(#duration(1,2,3,4))") == 2
    assert evaluate("Duration.Minutes(#duration(1,2,3,4))") == 3
    assert evaluate("Duration.Seconds(#duration(1,2,3,4))") == 4


def test_duration_components_negative_all_carry_the_sign():
    # A negative duration's component breakdown is signed throughout, not
    # Python timedelta's own "only days goes negative" normalisation -
    # pinned because it's exactly the kind of thing that's easy to get
    # subtly wrong by reaching for `timedelta.days`/`.seconds` directly.
    assert evaluate("Duration.Days(#duration(-1,-2,-3,-4))") == -1
    assert evaluate("Duration.Hours(#duration(-1,-2,-3,-4))") == -2
    assert evaluate("Duration.Minutes(#duration(-1,-2,-3,-4))") == -3
    assert evaluate("Duration.Seconds(#duration(-1,-2,-3,-4))") == -4


def test_duration_totals():
    assert evaluate("Duration.TotalDays(#duration(1,0,0,0))") == 1.0
    assert evaluate("Duration.TotalHours(#duration(1,2,3,4))") == pytest.approx(
        26.05111111111111
    )
    assert evaluate("Duration.TotalMinutes(#duration(0,1,0,0))") == 60.0
    assert evaluate("Duration.TotalSeconds(#duration(0,0,1,0))") == 60.0


def test_duration_to_text_default_format():
    assert evaluate("Duration.ToText(#duration(1,2,3,4))") == "1.02:03:04"
    assert evaluate("Duration.ToText(#duration(0,2,3,4))") == "02:03:04"
    # All-negative components (matches test_duration_components_negative_
    # all_carry_the_sign's #duration(-1,-2,-3,-4) case) - a duration whose
    # only negative component is days, e.g. #duration(-1,2,3,4), sums to a
    # different (still correct) total: -1 day + 2h3m4s = -21:56:56, not
    # "-1.02:03:04" - deliberately not asserted here to avoid conflating
    # "the days field is negative" with "the whole magnitude is negative".
    assert evaluate("Duration.ToText(#duration(-1,-2,-3,-4))") == "-1.02:03:04"
    assert evaluate("Duration.ToText(#duration(-1,0,0,0))") == "-1.00:00:00"


def test_duration_to_text_custom_format_is_unsupported():
    with pytest.raises(UnsupportedError):
        evaluate('Duration.ToText(#duration(1,0,0,0), "custom")')


def test_duration_to_text_null_propagates():
    assert evaluate("Duration.ToText(null)") is None


# --------------------------------------------------------------------------
# Time.*
# --------------------------------------------------------------------------


def test_time_from_text_and_passthrough():
    assert evaluate('Time.From("10:30:15")') == pydt.time(10, 30, 15)
    assert evaluate("Time.From(#time(10,30,15))") == pydt.time(10, 30, 15)


def test_time_from_datetime_extracts_time():
    assert evaluate("Time.From(#datetime(2024,1,31,10,30,15))") == pydt.time(10, 30, 15)


def test_time_from_null_propagates():
    assert evaluate("Time.From(null)") is None


def test_time_hour_minute_second():
    assert evaluate("Time.Hour(#time(10,30,15))") == 10
    assert evaluate("Time.Minute(#time(10,30,15))") == 30
    assert evaluate("Time.Second(#time(10,30,15))") == 15


def test_time_to_text_default_and_custom():
    assert evaluate("Time.ToText(#time(10,30,15))") == "10:30:15"
    assert evaluate('Time.ToText(#time(10,30,15), "HH:mm:ss")') == "10:30:15"
    assert evaluate('Time.ToText(#time(14,5,3), "hh:mm:ss tt")') == "02:05:03 PM"


def test_time_to_text_rejects_other_culture():
    with pytest.raises(UnsupportedError):
        evaluate('Time.ToText(#time(10,30,15), null, "fr-FR")')


# --------------------------------------------------------------------------
# Table.Sort / Text.From behave as documented for date-family values
# (module docstring points 1 and 2 - verified end-to-end, not asserted from
# the outside, since those functions live in files this task does not own)
# --------------------------------------------------------------------------


def test_table_sort_orders_a_date_column_correctly():
    query = (
        "let t = {[D = #date(2024,3,1)], [D = #date(2024,1,1)], "
        '[D = #date(2024,2,1)]} in Table.Sort(t, "D")'
    )
    result = evaluate(query)
    assert [row["D"] for row in result] == [
        pydt.date(2024, 1, 1),
        pydt.date(2024, 2, 1),
        pydt.date(2024, 3, 1),
    ]


def test_text_from_on_a_date_is_a_clean_refusal_not_a_silent_answer():
    # Matches real Power Query: Text.From does not accept date-family
    # values - Date.ToText exists for that. This is _text.py's own
    # behaviour (unowned by this module), verified end-to-end so a future
    # change to either module surfaces here if it regresses.
    with pytest.raises(EvalError, match="date"):
        evaluate("Text.From(#date(2024,1,31))")


# --------------------------------------------------------------------------
# Additional coercion, error-path, and format-token coverage
# --------------------------------------------------------------------------


def test_datetime_from_ole_serial_number():
    assert evaluate("DateTime.From(45000.5)") == pydt.datetime(2023, 3, 15, 12, 0, 0)


def test_datetime_from_rejects_logical():
    with pytest.raises(EvalError, match="logical"):
        evaluate("DateTime.From(true)")


def test_time_from_ole_day_fraction():
    assert evaluate("Time.From(0.25)") == pydt.time(6, 0, 0)


def test_time_from_rejects_logical():
    with pytest.raises(EvalError, match="logical"):
        evaluate("Time.From(false)")


def test_duration_from_rejects_logical():
    with pytest.raises(EvalError, match="logical"):
        evaluate("Duration.From(true)")


def test_date_from_rejects_bad_text():
    with pytest.raises(EvalError, match="ISO date"):
        evaluate('Date.From("not a date")')


def test_datetime_from_rejects_bad_text():
    with pytest.raises(EvalError, match="ISO datetime"):
        evaluate('DateTime.From("not a datetime")')


def test_time_from_rejects_bad_text():
    with pytest.raises(EvalError):
        evaluate('Time.From("not a time")')


def test_date_add_days_accepts_text_and_number():
    # Exercises _coerce_date_or_datetime's text and OLE-number branches,
    # not just the plain-`date`-in path every other Add* test uses.
    assert evaluate('Date.AddDays("2024-01-31", 1)') == pydt.date(2024, 2, 1)
    assert evaluate("Date.AddDays(45000, 1)") == pydt.date(2023, 3, 16)


def test_date_add_days_rejects_bad_text():
    with pytest.raises(EvalError):
        evaluate('Date.AddDays("not a date", 1)')


def test_date_add_years_out_of_range_is_eval_error():
    # `date`/`datetime` only support years 1-9999 - pushing past that must
    # be a clean EvalError, not an uncaught Python ValueError leaking out.
    with pytest.raises(EvalError):
        evaluate("Date.AddYears(#date(9999,6,1), 1)")


def test_date_to_text_rejects_non_text_non_record_option():
    with pytest.raises(EvalError):
        evaluate("Date.ToText(#date(2024,1,1), 42)")


def test_date_to_text_lone_hour_minute_second_tokens():
    # H/h/m/s (no doubling) are distinct tokens from HH/hh/mm/ss - pin all
    # four, plus fff, in one place since AM/PM (tt) and the doubled forms
    # are already covered elsewhere.
    text = evaluate('DateTime.ToText(#datetime(2024,1,1,9,5,3), "H:h:m:s")')
    assert text == "9:9:5:3"
    assert evaluate('DateTime.ToText(#datetime(2024,1,1,13,0,0), "H:h")') == "13:1"
    assert evaluate('DateTime.ToText(#datetime(2024,1,1,0,30,0.5), "fff")') == "500"


def test_date_to_text_lone_month_day_tokens():
    assert evaluate('Date.ToText(#date(2024,3,5), "M/d/yy")') == "3/5/24"
    assert evaluate('Date.ToText(#date(2024,3,5), "ddd")') in (
        "Mon",
        "Tue",
        "Wed",
        "Thu",
        "Fri",
        "Sat",
        "Sun",
    )


def test_duration_to_text_shows_fractional_seconds():
    result = evaluate("Duration.ToText(#duration(0,0,0,1.5))")
    assert result == "00:00:01.500000"


def test_literal_datetime_invalid_component_is_eval_error():
    with pytest.raises(EvalError, match="#datetime"):
        evaluate("#datetime(2024,1,1,25,0,0)")


def test_literal_time_invalid_component_is_eval_error():
    with pytest.raises(EvalError, match="#time"):
        evaluate("#time(25,0,0)")


def test_literal_datetimezone_invalid_component_is_eval_error():
    with pytest.raises(EvalError, match="#datetimezone"):
        evaluate("#datetimezone(2024,1,1,10,0,0,25,0)")


def test_date_end_of_week_with_explicit_first_day():
    wednesday = pydt.date(2024, 1, 10)
    end = evaluate(
        f"Date.EndOfWeek(#date({wednesday.year},{wednesday.month},{wednesday.day}), "
        "Day.Monday)"
    )
    assert end.weekday() == 6  # Sunday, the last day of a Monday-first week


def test_date_week_of_year_with_explicit_first_day():
    result = evaluate("Date.WeekOfYear(#date(2024,1,1), Day.Monday)")
    assert isinstance(result, int)
    assert result >= 1
