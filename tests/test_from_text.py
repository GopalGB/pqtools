"""The ``X.FromText`` family - text into a typed value.

These were missing entirely until now, which is a bigger hole than the count
suggests: `Number.FromText` is how a CSV column becomes numbers, so a query
doing the most ordinary thing in Power Query hit "unknown identifier". The
converters existed and were reachable only through `Table.TransformColumnTypes`.

Each `X.FromText` delegates to the same module's `X.From` rather than parsing
again. The agreement tests below exist to keep it that way: two parsers for
one family disagree eventually, and the failure shows up as a value that
converts one way through a column type and another way through a direct call.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pqtools import EvalError, UnsupportedError, evaluate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('Number.FromText("42")', 42),
        ('Number.FromText("3.5")', 3.5),
        ('Number.FromText("-7")', -7),
        ('Number.FromText("  7  ")', 7),
        ('Logical.FromText("true")', True),
        ('Logical.FromText("false")', False),
        ('Date.FromText("2026-01-09")', dt.date(2026, 1, 9)),
        ('Time.FromText("13:45:00")', dt.time(13, 45)),
        ('DateTime.FromText("2026-01-09T13:45:00")', dt.datetime(2026, 1, 9, 13, 45)),
        ('Duration.FromText("1.02:03:04")', dt.timedelta(days=1, seconds=7384)),
    ],
)
def test_from_text_converts(expression: str, expected: object) -> None:
    assert evaluate(expression) == expected


@pytest.mark.parametrize(
    "family", ["Number", "Logical", "Date", "Time", "DateTime", "Duration"]
)
def test_from_text_propagates_null(family: str) -> None:
    # null in, null out - M's convention throughout. Converting it to a zero
    # or an epoch date would silently invent data for every empty cell.
    assert evaluate(f"{family}.FromText(null)") is None


@pytest.mark.parametrize(
    ("expression", "wrong_type"),
    [
        ("Number.FromText(42)", "number"),
        ("Number.FromText(true)", "logical"),
        ("Date.FromText(#date(2026, 1, 9))", "date"),
        ("Duration.FromText(5)", "number"),
    ],
)
def test_from_text_requires_text(expression: str, wrong_type: str) -> None:
    """M draws this line too, and it is worth keeping.

    Accepting a number here would let `Number.FromText` report a successful
    text conversion on a column that never contained text - the failure mode
    where the answer looks right and the pipeline is wrong.
    """
    with pytest.raises(EvalError, match=f"expected text, got {wrong_type}"):
        evaluate(expression)


@pytest.mark.parametrize(
    ("text_call", "from_call"),
    [
        ('Number.FromText("42")', 'Number.From("42")'),
        ('Number.FromText("3.5")', 'Number.From("3.5")'),
        ('Date.FromText("2026-01-09")', 'Date.From("2026-01-09")'),
        ('Time.FromText("13:45:00")', 'Time.From("13:45:00")'),
        ('Logical.FromText("true")', 'Logical.From("true")'),
        ('Duration.FromText("1.02:03:04")', 'Duration.From("1.02:03:04")'),
    ],
)
def test_from_text_agrees_with_from(text_call: str, from_call: str) -> None:
    """One parser per family, checked rather than assumed."""
    assert evaluate(text_call) == evaluate(from_call)


def test_from_text_reports_itself_not_the_function_it_delegates_to() -> None:
    # An error naming `Number.From` when the query says `Number.FromText`
    # sends the reader hunting for a call that is not there.
    with pytest.raises(EvalError, match=r"Number\.FromText: not a number"):
        evaluate('Number.FromText("abc")')


@pytest.mark.parametrize("family", ["Number", "Date", "DateTime", "Time"])
def test_from_text_refuses_a_foreign_culture_rather_than_guessing(family: str) -> None:
    """ "1.234,5" is 1234.5 in de-DE and 1.2345 read as en-US.

    Both parse. Only one is right, and nothing downstream can tell which
    happened, so the refusal has to come before the parse.
    """
    with pytest.raises(UnsupportedError, match="culture-specific parsing"):
        evaluate(f'{family}.FromText("1", "de-DE")')


@pytest.mark.parametrize("family", ["Number", "Date"])
def test_from_text_accepts_an_invariant_culture(family: str) -> None:
    assert evaluate(f'{family}.FromText(null, "en-US")') is None


def test_from_text_closes_the_csv_round_trip() -> None:
    """The gap that prompted this: text columns off a CSV becoming numbers."""
    source = """
    let
        Source   = Csv.Document("amount#(lf)10#(lf)32#(lf)"),
        Promoted = Table.PromoteHeaders(Source),
        Typed    = Table.TransformColumns(Promoted, {{"amount", Number.FromText}})
    in
        List.Sum(Table.Column(Typed, "amount"))
    """
    assert evaluate(source) == 42


# --------------------------------------------------------------------------
# The second parameter, per each function's own Syntax block
#
# It is not uniform across the family, and pqtools had assumed it was: every
# X.FromText read argument two as a bare culture string. That got three
# things wrong at once - the documented options record was rejected with
# "expected text, got record", and Duration/Logical accepted a second
# argument they do not have.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression",
    [
        'Date.FromText("2010-12-31", [Culture="en-US"])',
        'Date.FromText("2010-12-31", [Format=null, Culture="en-US"])',
        'DateTime.FromText("2010-12-31T01:30:00", [Culture="en-US"])',
        'Time.FromText("01:30:00", [Culture="en-US"])',
    ],
)
def test_the_documented_options_record_is_accepted(expression: str) -> None:
    """ "options: An optional record ... Format ... Culture".

    This died with "expected text, got record" - an error naming the wrong
    problem, on the shape the reference documents first.
    """
    assert evaluate(expression) is not None


def test_the_legacy_text_form_still_works() -> None:
    """ "To support legacy workflows, options can also be a text value. This
    has the same behavior as if options = [Format = null, Culture = options]."
    """
    assert evaluate('Date.FromText("2010-12-31", "en-US")') == dt.date(2010, 12, 31)
    assert evaluate('Date.FromText("2010-12-31")') == dt.date(2010, 12, 31)


def test_a_format_string_is_honoured_not_ignored() -> None:
    """This test used to pin the refusal. The refusal was the defect.

    `Date.ToText(d, "yyyy-MM-dd")` had worked all along, so a query could
    write a date out with a pattern and then not read it back with the same
    one - and three of Microsoft's four `DateTime.FromText` examples are
    Format-string calls. Ignoring the field would still be wrong (a
    best-effort parse returns a value the caller never asked for), so the
    field is now obeyed; `tests/test_datetime_format_parsing.py` covers the
    engine, and what remains refused is named there.
    """
    assert evaluate('Date.FromText("2010-12-31", [Format="yyyy-MM-dd"])') == dt.date(
        2010, 12, 31
    )
    with pytest.raises(UnsupportedError, match="format specifier"):
        evaluate('Date.FromText("2010-12-31", [Format="QQQ"])')


def test_an_unknown_option_is_named() -> None:
    with pytest.raises(UnsupportedError, match=r"Nonsense"):
        evaluate('Date.FromText("2010-12-31", [Nonsense=1])')


@pytest.mark.parametrize(
    "expression",
    ['Duration.FromText("1:00", "en-US")', 'Logical.FromText("true", "en-US")'],
)
def test_the_one_argument_signatures_reject_a_second(expression: str) -> None:
    """`Duration.FromText(text as nullable text) as nullable duration`.

    Same for Logical.FromText. Accepting an argument the function does not
    have is the same defect as an invented function name: the call runs here
    and fails in Power Query, which no test of the function's own behaviour
    would ever catch.
    """
    with pytest.raises(UnsupportedError, match="with 2 argument"):
        evaluate(expression)


def test_number_from_text_takes_a_culture_not_a_record() -> None:
    """`Number.FromText(text, optional culture as nullable text)`.

    Its date siblings take a record; this one does not, and saying so beats
    accepting a record here that Power Query would reject.
    """
    assert evaluate('Number.FromText("15", "en-US")') == 15
    with pytest.raises(EvalError, match="culture text value, not an options record"):
        evaluate('Number.FromText("15", [Culture="en-US"])')


# --------------------------------------------------------------------------
# Duration.FromText's own documented grammar
#
#     (-)hh:mm(:ss(.ff))
#     (-)ddd(.hh:mm(:ss(.ff)))
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The page's worked example.
        ("2.05:55:20", dt.timedelta(days=2, hours=5, minutes=55, seconds=20)),
        # hh:mm - seconds are optional, and this was rejected outright.
        ("1:00", dt.timedelta(hours=1)),
        ("01:30", dt.timedelta(hours=1, minutes=30)),
        ("-1:30", -dt.timedelta(hours=1, minutes=30)),
        # ddd alone, and ddd.hh:mm without seconds - both also rejected.
        ("5", dt.timedelta(days=5)),
        ("-5", -dt.timedelta(days=5)),
        ("1.02:03", dt.timedelta(days=1, hours=2, minutes=3)),
        # The forms that already worked, kept as the control.
        ("1:30:45", dt.timedelta(hours=1, minutes=30, seconds=45)),
        ("1:30:45.25", dt.timedelta(hours=1, minutes=30, seconds=45.25)),
        ("00:00:00", dt.timedelta(0)),
    ],
)
def test_every_documented_duration_format_parses(
    text: str, expected: dt.timedelta
) -> None:
    assert evaluate(f'Duration.FromText("{text}")') == expected


@pytest.mark.parametrize("text", ["25:00", "1:60", "0:00:60"])
def test_the_documented_ranges_are_enforced(text: str) -> None:
    """ "hh: Number of hours, between 0 and 23", mm and ss between 0 and 59.

    Rolling 25:00 over into a day would accept text the real function
    rejects - a query green here and broken there.
    """
    with pytest.raises(EvalError, match="out of range"):
        evaluate(f'Duration.FromText("{text}")')
