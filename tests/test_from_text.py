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
