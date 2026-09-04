"""``table{[Field=value]}`` - record-match row selection.

This is the second line of nearly every query Power Query's own UI writes:

    Source = Excel.Workbook(File.Contents(path), null, true),
    Sheet  = Source{[Item="Colors", Kind="Sheet"]}[Data]

Until 0.9.0 the record selector fell through to the positional-index path and
the query died with "expected a number, got record" - an error naming the
wrong problem, on the line every generated workbook query reaches first.

It survived a thousand passing tests because every fixture in the suite was
written by hand, and by hand you reach for `Table.SelectRows`. Nobody writes
`{[Item=...]}` unless they are pasting what Power Query produced, which is
exactly the input this tool exists to accept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pqtools import EvalError, evaluate

T = '#table({"Item","Kind","Data"},{{"Colors","Sheet",1},{"Stock","Sheet",2}})'


def test_selects_the_matching_row() -> None:
    assert evaluate(f'let S = {T} in S{{[Item="Colors"]}}') == {
        "Item": "Colors",
        "Kind": "Sheet",
        "Data": 1,
    }


def test_all_named_fields_must_match() -> None:
    assert evaluate(f'let S = {T} in S{{[Item="Stock", Kind="Sheet"]}}[Data]') == 2


def test_field_projection_off_the_matched_row() -> None:
    assert evaluate(f'let S = {T} in S{{[Item="Colors"]}}[Data]') == 1


def test_a_key_matching_nothing_is_an_error() -> None:
    with pytest.raises(EvalError, match="no row matches"):
        evaluate(f'let S = {T} in S{{[Item="Nope"]}}')


def test_a_key_matching_nothing_is_null_when_optional() -> None:
    assert evaluate(f'let S = {T} in S{{[Item="Nope"]}}?') is None


def test_a_key_matching_several_rows_is_an_error() -> None:
    """`?` does not license picking one.

    It means "this key may be absent", not "choose a match for me". Returning
    the first would work until the data grew a second match, and then quietly
    return a different row.
    """
    with pytest.raises(EvalError, match="2 rows match"):
        evaluate(f'let S = {T} in S{{[Kind="Sheet"]}}')


def test_a_key_matching_several_rows_errors_even_when_optional() -> None:
    with pytest.raises(EvalError, match="2 rows match"):
        evaluate(f'let S = {T} in S{{[Kind="Sheet"]}}?')


def test_a_field_absent_from_the_row_does_not_match() -> None:
    with pytest.raises(EvalError, match="no row matches"):
        evaluate(f'let S = {T} in S{{[Missing="x"]}}')


def test_positional_indexing_still_works() -> None:
    # The record path must not have captured the ordinary case.
    assert evaluate(f"let S = {T} in S{{0}}[Item]") == "Colors"
    assert evaluate("{10, 20, 30}{1}") == 20
    with pytest.raises(EvalError, match="out of range"):
        evaluate("{10, 20, 30}{9}")


def test_the_shape_power_query_actually_generates(tmp_path: Path) -> None:
    """The end-to-end case, verbatim from a generated workbook query."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Colors"
    for row in (["ColorID", "Color"], [1, "Red"], [2, "Blue"]):
        sheet.append(row)
    book.create_sheet("Stock")
    path = tmp_path / "report.xlsx"
    book.save(path)

    source = f"""
    let
        Source = Excel.Workbook(File.Contents("{path}"), null, true),
        Colors_Sheet = Source{{[Item="Colors",Kind="Sheet"]}}[Data],
        #"Promoted Headers" = Table.PromoteHeaders(
            Colors_Sheet, [PromoteAllScalars=true]
        ),
        #"Changed Type" = Table.TransformColumnTypes(
            #"Promoted Headers", {{{{"ColorID", Int64.Type}}, {{"Color", type text}}}}
        )
    in
        #"Changed Type"
    """
    assert evaluate(source) == [
        {"ColorID": 1, "Color": "Red"},
        {"ColorID": 2, "Color": "Blue"},
    ]
