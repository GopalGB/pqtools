"""M's `#(...)` character escapes inside text literals.

Until 0.9.0 these were not decoded at all: `"a#(lf)b"` evaluated to the eight
literal characters `a#(lf)b`. That is the worst shape of bug this package can
have, because nothing errors. `Text.Split(x, "#(lf)")` simply never matched,
so a query that should return many rows returned one, and the number looked
like a data problem rather than a pqtools problem.

It survived 970-odd passing tests for the same reason `#table` did: every
fixture was written by someone reaching for Python's `\\n`, while every real M
query writes `#(lf)`. The suite tested the library's habits, not the
language's.
"""

from __future__ import annotations

import pytest

from pqtools import EvalError, evaluate


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ('"a#(lf)b"', "a\nb"),
        ('"a#(cr)b"', "a\rb"),
        ('"a#(tab)b"', "a\tb"),
        ('"#(cr,lf)"', "\r\n"),  # one sequence, two characters
        ('"a#(lf)#(lf)b"', "a\n\nb"),
        ('"#(0041)"', "A"),
        ('"#(00E9)"', "é"),
        ('"#(0001F600)"', "\U0001f600"),  # 8 hex digits: astral plane
        ('"#(#)"', "#"),
        ('"#(lf,tab,cr)"', "\n\t\r"),
    ],
)
def test_escape_sequences_decode(literal: str, expected: str) -> None:
    assert evaluate(literal) == expected


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ('"a#b"', "a#b"),  # bare # is an ordinary character
        ('"a##b"', "a##b"),  # M has no `##` escape
        ('"#"', "#"),
        ('"100# discount"', "100# discount"),
        ('"say ""hi"""', 'say "hi"'),  # doubled quote, unchanged behaviour
        ('""', ""),
    ],
)
def test_text_without_escapes_is_untouched(literal: str, expected: str) -> None:
    """The decoder must not start eating `#` characters that mean themselves."""
    assert evaluate(literal) == expected


@pytest.mark.parametrize(
    "literal",
    ['"#(bogus)"', '"#(zz)"', '"#(041)"', '"#()"', '"#(D800)"', '"#(00110000)"'],
)
def test_a_malformed_escape_is_an_error_not_a_passthrough(literal: str) -> None:
    """Refusing loudly is the whole improvement.

    Passing an unrecognised escape through unchanged is what produced the
    original bug: the query kept running and quietly compared against the
    wrong text. An error names the problem at the line that caused it.
    """
    with pytest.raises(EvalError):
        evaluate(literal)


def test_unterminated_escape_is_reported() -> None:
    with pytest.raises(EvalError, match="unterminated escape"):
        evaluate('"a#(lf"')


# --------------------------------------------------------------------------
# The idioms that actually broke
# --------------------------------------------------------------------------


def test_splitting_on_a_line_feed_is_how_m_writes_it() -> None:
    assert evaluate('Text.Split("a#(lf)b#(lf)c", "#(lf)")') == ["a", "b", "c"]


def test_lines_from_text_over_an_escaped_document() -> None:
    assert evaluate('Lines.FromText("one#(cr,lf)two")') == ["one", "two"]


def test_csv_document_over_an_escaped_document() -> None:
    # The exact shape that exposed the bug: a CSV written inline, the way
    # every Power Query tutorial writes one.
    source = """
    let
        Source   = Csv.Document("amount#(lf)10#(lf)32"),
        Promoted = Table.PromoteHeaders(Source)
    in
        List.Sum(List.Transform(Table.Column(Promoted, "amount"), Number.FromText))
    """
    assert evaluate(source) == 42


def test_a_combined_text_round_trips_through_a_split() -> None:
    source = (
        'let joined = Text.Combine({"a", "b", "c"}, "#(lf)") '
        'in Text.Split(joined, "#(lf)")'
    )
    assert evaluate(source) == ["a", "b", "c"]
