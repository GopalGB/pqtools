"""``X.FromText(text, [Format = ...])`` - the half of the round trip that
was an ``UnsupportedError`` while the formatting half was complete.

Why this file exists at all: `Date.ToText(d, "dd MMM yyyy")` worked and
`Date.FromText(s, [Format = "dd MMM yyyy"])` did not, so a query could
write a date out and then be unable to read it back with the pattern it
had just used. Microsoft's own reference pages spend three of their four
`DateTime.FromText` examples on a Format string, which is how the gap was
found: the doc-example corpus ran them and every one refused.

The tests below are grouped by the thing that can go wrong, not by
function, because the four families share one engine and a bug in the
tokenizer shows up in all four at once.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from pqtools import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# The examples Microsoft's own pages carry
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # datetime-fromtext, examples 3 and 4 verbatim (the two whose
        # Culture is en-US, so nothing but the Format was ever missing).
        (
            'DateTime.FromText("2000-02-08T03:45:12Z", '
            "[Format=\"yyyy-MM-dd'T'HH:mm:ss'Z'\", Culture=\"en-US\"])",
            datetime(2000, 2, 8, 3, 45, 12),
        ),
        (
            'DateTime.FromText("20101231T013000", '
            '[Format="yyyyMMdd\'T\'HHmmss", Culture="en-US"])',
            datetime(2010, 12, 31, 1, 30, 0),
        ),
        # datetimezone-fromtext, example 3: the standard round-trip "O"
        # format, whose offset may be a literal "Z" and so is not
        # expressible as a custom pattern.
        (
            'DateTimeZone.FromText("2009-06-15T13:45:30.0000000-07:00", '
            '[Format="O", Culture="en-US"])',
            datetime(2009, 6, 15, 13, 45, 30, tzinfo=timezone(timedelta(hours=-7))),
        ),
    ],
)
def test_a_documented_example_parses(source: str, expected: object) -> None:
    assert evaluate(source) == expected


def test_a_documented_example_that_needs_a_culture_still_says_so() -> None:
    """The other three examples are de-DE/ar-SA and stay honest refusals.

    Implementing the Format string must not turn "this needs German month
    names" into a wrong answer - the parser only knows en-US names, so the
    culture check has to fire before the pattern is ever compiled.
    """
    with pytest.raises(UnsupportedError, match="culture-specific parsing"):
        evaluate(
            'Date.FromText("30 Dez 2010", [Format="dd MMM yyyy", Culture="de-DE"])'
        )


# --------------------------------------------------------------------------
# The tokenizer - it must agree with the renderer's, token for token
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fmt", "expected"),
    [
        # Adjacent tokens with no separator: .NET reads "yyyyMMdd" as three
        # tokens, not one 8-letter blob, so a greedy \d+ would be wrong.
        ("20101231", "yyyyMMdd", date(2010, 12, 31)),
        # Single-letter specifiers mean "no leading zero" on the way OUT,
        # so on the way in they have to accept one digit or two.
        ("1/2/2020", "M/d/yyyy", date(2020, 1, 2)),
        ("12/31/2020", "M/d/yyyy", date(2020, 12, 31)),
        ("31 Dec 2010", "dd MMM yyyy", date(2010, 12, 31)),
        ("31 December 2010", "dd MMMM yyyy", date(2010, 12, 31)),
        # A quoted run is literal text, never scanned for tokens - without
        # that rule the "T" and "Z" below would read as day and zone.
        ("2010-12-31", "yyyy-MM-dd", date(2010, 12, 31)),
    ],
)
def test_a_date_pattern_reads_back(text: str, fmt: str, expected: date) -> None:
    assert evaluate(f'Date.FromText("{text}", [Format="{fmt}"])') == expected


def test_a_two_digit_year_uses_dot_nets_1930_2029_window() -> None:
    """ "29" is 2029 and "30" is 1930 - .NET's invariant TwoDigitYearMax.

    Reading a two-digit year as "the current century" instead would make
    the same text mean different things in different decades, which is the
    same host dependence this module refuses elsewhere.
    """
    assert evaluate('Date.FromText("01-01-29", [Format="dd-MM-yy"])') == date(
        2029, 1, 1
    )
    assert evaluate('Date.FromText("01-01-30", [Format="dd-MM-yy"])') == date(
        1930, 1, 1
    )


def test_a_twelve_hour_clock_needs_its_designator() -> None:
    assert evaluate('Time.FromText("01:30 PM", [Format="hh:mm tt"])') == time(13, 30)
    assert evaluate('Time.FromText("01:30 AM", [Format="hh:mm tt"])') == time(1, 30)
    # 12 AM is midnight, not hour 12 - the modulo is the whole point.
    assert evaluate('Time.FromText("12:00 AM", [Format="hh:mm tt"])') == time(0, 0)
    assert evaluate('Time.FromText("12:00 PM", [Format="hh:mm tt"])') == time(12, 0)


def test_every_fraction_width_one_through_seven_round_trips() -> None:
    """The renderer knew only "fff" and "fffffff".

    .NET spells the fractional-seconds specifier one to seven letters
    wide, and Microsoft's own DateTime.FromText example uses "ffffff" -
    six - which neither direction could handle. A fixture author writing
    milliseconds reaches for "fff"; the reference does not.
    """
    for width in range(1, 8):
        token = "f" * width
        rendered = evaluate(f'Time.ToText(#time(1, 2, 3), "HH:mm:ss.{token}")')
        assert rendered == "01:02:03." + "0" * width
    # And a real fraction survives the trip at microsecond precision.
    assert evaluate(
        'Time.FromText("01:02:03.369730", [Format="HH:mm:ss.ffffff"])'
    ) == time(1, 2, 3, 369730)
    # The 7th digit is 100-nanosecond ticks, which a Python time cannot
    # hold, so it truncates rather than rounding into the next microsecond.
    assert evaluate(
        'Time.FromText("01:02:03.3697309", [Format="HH:mm:ss.fffffff"])'
    ) == time(1, 2, 3, 369730)


def test_an_unknown_specifier_is_refused_whole() -> None:
    """ "QQQ" must not split into a known prefix plus stray literal letters."""
    with pytest.raises(UnsupportedError, match="format specifier 'QQQ'"):
        evaluate('Date.FromText("2010", [Format="QQQ"])')
    with pytest.raises(UnsupportedError, match="format specifier 'yyy'"):
        evaluate('Date.FromText("2010", [Format="yyy"])')


# --------------------------------------------------------------------------
# What the parser refuses, and why each refusal is the right answer
# --------------------------------------------------------------------------


def test_a_time_only_pattern_is_refused_rather_than_dated_from_the_clock() -> None:
    """Real .NET fills the missing date from TODAY. That is not reproducible.

    `DateTime.FromText("013000", [Format = "HHmmss"])` would return a
    different value tomorrow. This module already refuses the "U" standard
    format for depending on the machine's time zone; the same rule applies
    to depending on the machine's date.
    """
    with pytest.raises(UnsupportedError, match="machine's clock"):
        evaluate('DateTime.FromText("013000", [Format="HHmmss"])')


def test_a_partial_date_defaults_to_january_first_not_to_today() -> None:
    """ "yyyy" alone is a documented example, so it has to produce a value."""
    assert evaluate('Date.FromText("1400", [Format="yyyy"])') == date(1400, 1, 1)


def test_a_weekday_that_contradicts_the_date_is_an_error() -> None:
    """A day name is data, not decoration - .NET validates it too.

    Accepting it silently would let a typo'd export parse cleanly into the
    wrong day of the week with nothing to show for it.
    """
    assert evaluate('Date.FromText("Friday, December 31, 2010", [Format="D"])') == date(
        2010, 12, 31
    )
    with pytest.raises(EvalError, match="names a weekday"):
        evaluate('Date.FromText("Saturday, December 31, 2010", [Format="D"])')


def test_text_that_does_not_match_the_pattern_says_so() -> None:
    with pytest.raises(EvalError, match="does not match format"):
        evaluate('Date.FromText("31/12/2010", [Format="yyyy-MM-dd"])')


def test_an_impossible_date_or_time_is_an_error_not_a_rollover() -> None:
    with pytest.raises(EvalError, match="not a real date"):
        evaluate('Date.FromText("31 Feb 2010", [Format="dd MMM yyyy"])')
    with pytest.raises(EvalError, match="not a real time"):
        evaluate('Time.FromText("25:00:00", [Format="HH:mm:ss"])')
    with pytest.raises(EvalError, match="not a real time"):
        evaluate(
            'DateTime.FromText("2010-12-31 24:00:00", [Format="yyyy-MM-dd HH:mm:ss"])'
        )


def test_datetimezone_needs_an_offset_and_datetime_drops_one() -> None:
    """The return TYPE decides, not the pattern.

    `DateTime.FromText` returns a naive datetime in this module's value
    model, so a `zzz` in its pattern is read (the text still has to match)
    and then discarded. `DateTimeZone.FromText` has the opposite rule: no
    offset in the pattern means it cannot produce its own return type.
    """
    assert evaluate(
        'DateTime.FromText("2010-12-31 01:30 +02:00", [Format="yyyy-MM-dd HH:mm zzz"])'
    ) == datetime(2010, 12, 31, 1, 30)
    with pytest.raises(EvalError, match="no time-zone offset"):
        evaluate('DateTimeZone.FromText("2010-12-31", [Format="yyyy-MM-dd"])')


def test_the_offset_sign_survives_a_half_hour_zone() -> None:
    """+05:30 and -03:30 are where a minutes-are-always-zero bug hides."""
    assert evaluate(
        'DateTimeZone.FromText("2010-12-31 01:30 +05:30", '
        '[Format="yyyy-MM-dd HH:mm zzz"])'
    ) == datetime(2010, 12, 31, 1, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert evaluate(
        'DateTimeZone.FromText("2010-12-31 01:30 -03:30", '
        '[Format="yyyy-MM-dd HH:mm zzz"])'
    ) == datetime(2010, 12, 31, 1, 30, tzinfo=timezone(-timedelta(hours=3, minutes=30)))


def test_a_family_without_a_documented_format_option_still_refuses_one() -> None:
    """Number/Duration/Logical.FromText take no Format at all.

    Growing a parser for the temporal families must not quietly hand one
    to families whose Syntax block has no such parameter - accepting an
    argument M does not have is the same defect class as an invented
    function name.
    """
    with pytest.raises((EvalError, UnsupportedError)):
        evaluate('Number.FromText("4", [Format="0.00"])')


# --------------------------------------------------------------------------
# The round trip, stated as one property
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt",
    [
        "yyyy-MM-dd",
        "dd MMM yyyy",
        "dddd, MMMM d, yyyy",
        "M/d/yyyy",
        "yyyyMMdd",
    ],
)
def test_what_to_text_writes_from_text_reads_back(fmt: str) -> None:
    """The two directions share a tokenizer so that this cannot drift.

    When they had separate ones, only the writing half existed; the point
    of building the reader from the same token table is that a value can
    make the trip and come back unchanged.
    """
    rendered = evaluate(f'Date.ToText(#date(2010, 12, 31), "{fmt}")')
    assert evaluate(f'Date.FromText("{rendered}", [Format="{fmt}"])') == date(
        2010, 12, 31
    )
