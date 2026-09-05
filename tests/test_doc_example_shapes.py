"""Argument shapes Microsoft's own examples use, that this suite never did.

Every case below was found the same way: by running the reference pages'
worked examples and reading what blew up. None of them is exotic. They are
a null in a list of names, a list of characters to strip, a percentage
typed as text, a time converted to a datetime, a record type used as a
field list, and a date written the way an American writes one.

The pattern is the one this repo keeps rediscovering: a fixture author
reaches for the clean shape - all text, no nulls, ISO dates - and the
reference reaches for the shape a real column actually holds. The tests
here are the reference's shapes, each with the page and example that
states the answer.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pqtools import EvalError, evaluate

# --------------------------------------------------------------------------
# Text.Combine - "Note that the null is ignored"
# --------------------------------------------------------------------------


def test_text_combine_ignores_nulls() -> None:
    """text-combine, example 3, whose stated output is "Seattle, WA".

    Not "Seattle, , WA" - the value is dropped before the separator is
    applied, so a missing item leaves no gap. This raised "expected text,
    got null".
    """
    assert evaluate('Text.Combine({"Seattle", null, "WA"}, ", ")') == "Seattle, WA"
    assert evaluate('Text.Combine({"Seattle", "WA"})') == "SeattleWA"
    assert evaluate("Text.Combine({null, null})") == ""


def test_text_combine_null_handling_is_what_a_name_join_needs() -> None:
    """text-combine, example 4: first / middle initial / last.

    A missing middle initial is the ordinary row, not the edge case, and
    the stated Full Name for it is "Rada Mihaylova" with one space.
    """
    source = (
        "let Source = Table.FromRecords({"
        '[First Name = "Doug", Middle Initial = "J", Last Name = "Elis"],'
        '[First Name = "Rada", Middle Initial = null, Last Name = "Mihaylova"]'
        '}) in Table.AddColumn(Source, "Full Name", each '
        'Text.Combine({[First Name], [Middle Initial], [Last Name]}, " "))'
    )
    assert [row["Full Name"] for row in evaluate(source)] == [
        "Doug J Elis",
        "Rada Mihaylova",
    ]


# --------------------------------------------------------------------------
# Text.Trim / TrimStart / TrimEnd - the trim argument may be a LIST
# --------------------------------------------------------------------------


def test_text_trim_accepts_text_or_a_list_of_characters() -> None:
    """text-trim, examples 2 and 3, with their stated outputs.

    The list shape raised "expected text, got list", although `_char_set`
    had been resolving exactly these two shapes for Text.Select and
    Text.Remove since 0.5.0.
    """
    assert evaluate('Text.Trim("0000056.4200", "0")') == "56.42"
    assert evaluate('Text.Trim("<div/>", {"<", ">", "/"})') == "div"
    assert evaluate('Text.Trim("     a b c d    ")') == "a b c d"


def test_text_trim_start_and_end_take_the_same_argument() -> None:
    assert evaluate('Text.TrimStart("##a##", {"#"})') == "a##"
    assert evaluate('Text.TrimEnd("##a##", {"#"})') == "##a"


def test_the_trim_columns_example_runs() -> None:
    """text-trim, example 4 - the same call inside Table.TransformColumns."""
    source = (
        "let Source = #table(type table [Status = text], "
        '{{"##@@Pending@@##"}, {"Sold"}}) in '
        'Table.TransformColumns(Source, {"Status", each Text.Trim(_, {"#", "@"})})'
    )
    assert [row["Status"] for row in evaluate(source)] == ["Pending", "Sold"]


# --------------------------------------------------------------------------
# Number.From on a percentage
# --------------------------------------------------------------------------


def test_number_from_reads_a_percentage() -> None:
    """number-from, example 3: "12.3%" -> 0.123.

    Exactly 0.123, the double a reader gets by typing it - not
    `12.3 / 100`, which is 0.12300000000000001. The digits are shifted in
    decimal before the value becomes a float.
    """
    assert evaluate('Number.From("12.3%")') == 0.123
    assert evaluate('Number.From("-5.5%")') == -0.055
    assert evaluate('Number.From("4")') == 4
    with pytest.raises(EvalError, match="not a number"):
        evaluate('Number.From("12.3%%")')


# --------------------------------------------------------------------------
# DateTime.From on a time
# --------------------------------------------------------------------------


def test_datetime_from_a_time_uses_the_ole_epoch() -> None:
    """datetime-from, example 1, stated as #datetime(1899, 12, 30, 6, 45, 12).

    DateTimeZone.From already did exactly this for a bare time; DateTime.From
    raised "expected a datetime, got time".
    """
    assert evaluate("DateTime.From(#time(06, 45, 12))") == dt.datetime(
        1899, 12, 30, 6, 45, 12
    )
    assert evaluate("DateTime.From(#date(1975, 4, 4))") == dt.datetime(1975, 4, 4)


def test_datetime_date_still_refuses_a_time() -> None:
    """The coercion helper was deliberately left alone.

    Only DateTime.From documents the time conversion. Widening the shared
    helper would have made DateTime.Date(#time(...)) answer 1899-12-30,
    which real Power Query does not.
    """
    with pytest.raises(EvalError, match="expected a datetime, got time"):
        evaluate("DateTime.Date(#time(06, 45, 12))")


# --------------------------------------------------------------------------
# Record.FromList with a record type
# --------------------------------------------------------------------------


def test_record_from_list_accepts_a_record_type() -> None:
    """record-fromlist, example 2.

    This refused by name on the grounds that a `type [...]` expression did
    not evaluate here. It does now, so the refusal outlived its reason.
    """
    assert evaluate(
        'Record.FromList({1, "Bob", "123-4567"}, '
        "type [CustomerID = number, Name = text, Phone = number])"
    ) == {"CustomerID": 1, "Name": "Bob", "Phone": "123-4567"}


def test_record_from_list_does_not_check_the_declared_types() -> None:
    """The example itself passes "123-4567" for `Phone = number`.

    M takes the names from the type and does not validate against it, and
    the page states the record that comes back. Enforcing the types here
    would fail Microsoft's own example.
    """
    assert evaluate('Record.FromList({"x"}, type [Only = number])') == {"Only": "x"}


def test_record_from_list_rejects_a_non_record_type_by_name() -> None:
    with pytest.raises(EvalError, match="expected a record type"):
        evaluate("Record.FromList({1}, type number)")


# --------------------------------------------------------------------------
# The default (no Format, no Culture) date and time parse
# --------------------------------------------------------------------------


def test_a_bare_date_from_reads_the_en_us_patterns_not_only_iso() -> None:
    """list-contains and list-containsany, example 4 of each.

    `Date.From("4/8/2022")` and `Date.From("Apr 8, 2022")` both appear with
    a stated output of `true`, and both raised "not a valid ISO date" -
    ISO was the only shape this module could read, which is the narrowest
    possible reading of "the invariant culture".
    """
    assert evaluate('Date.From("4/8/2022")') == dt.date(2022, 4, 8)
    assert evaluate('Date.From("Apr 8, 2022")') == dt.date(2022, 4, 8)
    assert evaluate('Date.From("April 8, 2022")') == dt.date(2022, 4, 8)
    assert evaluate('Date.From("8 April 2022")') == dt.date(2022, 4, 8)


def test_iso_is_still_tried_first() -> None:
    """So an unambiguous ISO date can never be re-read month-first."""
    assert evaluate('Date.From("2022-04-08")') == dt.date(2022, 4, 8)
    assert evaluate('Date.FromText("2010-12-31")') == dt.date(2010, 12, 31)


def test_the_list_contains_example_runs() -> None:
    assert evaluate(
        "let Source = {#date(2024, 2, 23), #date(2022, 4, 8)} in "
        'List.Contains(Source, Date.From("4/8/2022"))'
    )


def test_time_from_text_reads_an_unspaced_designator() -> None:
    """time-fromtext, example 1: "10:12:31am", no space before the "am"."""
    assert evaluate('Time.FromText("10:12:31am")') == dt.time(10, 12, 31)
    assert evaluate('Time.FromText("10:12:31 PM")') == dt.time(22, 12, 31)
    assert evaluate('Time.FromText("1012")') == dt.time(10, 12)


def test_unparseable_text_still_says_so() -> None:
    with pytest.raises(EvalError, match="not a recognisable date"):
        evaluate('Date.From("nonsense")')
    with pytest.raises(EvalError, match="not a recognisable time"):
        evaluate('Time.FromText("nonsense")')


# --------------------------------------------------------------------------
# Function.ScalarVector - the one-row table's column names
# --------------------------------------------------------------------------


def test_scalar_vector_names_the_columns_after_the_type_s_parameters() -> None:
    """function-scalarvector, example 1, with its stated Result column.

    The one-row table handed to vectorFunction was built with invented
    column names (Column1, Column2) because `type function (...)` did not
    evaluate when this was written. It does now - and both worked examples
    index that table as `[left]` and `[right]`, so the invented names
    failed with "field not found: left". A positional guess that LOOKS like
    a table is the worst shape to hand a caller about to index it by name.
    """
    source = """let
    Compute.ScoreScalar = (left, right) => left * right,
    Compute.ScoreVector = (input) => let
        chunks = Table.Split(input, 100),
        scoreChunk = (chunk) =>
            Table.TransformRows(chunk, each Compute.ScoreScalar([left], [right]))
      in
        List.Combine(List.Transform(chunks, scoreChunk)),
    Compute.Score = Function.ScalarVector(
        type function (left as number, right as number) as number,
        Compute.ScoreVector
    ),
    Final = Table.AddColumn(
        Table.FromRecords({[a = 1, b = 2], [a = 3, b = 4]}),
        "Result",
        each Compute.Score([a], [b])
    )
in
    Final"""
    assert evaluate(source) == [
        {"a": 1, "b": 2, "Result": 2},
        {"a": 3, "b": 4, "Result": 12},
    ]
