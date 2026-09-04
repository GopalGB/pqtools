"""Local-file connectors: File.Contents / Csv.Document / Text.FromBinary.

The point of these is that a query copied verbatim out of Power Query's
Advanced Editor - connector step included - runs without ``--bind``. The
cases worth guarding are the ones where being wrong is quiet: header
promotion, short-row padding, BOM handling, and the boundary that must
still refuse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pqtools.builtins import BUILTINS
from pqtools.builtins._shared import EvalError, UnsupportedError
from pqtools.evaluate import evaluate


def _call(name: str, *args: object) -> object:
    return BUILTINS[name](list(args), None)


# --- Csv.Document -------------------------------------------------------


def test_csv_document_does_not_promote_headers() -> None:
    # The single most important behaviour: Csv.Document yields Column1..N and
    # leaves promotion to Table.PromoteHeaders. Auto-promoting here would
    # break every real query, which always has that step.
    assert _call("Csv.Document", "OrderID,Item\r\n1,Rod") == [
        {"Column1": "OrderID", "Column2": "Item"},
        {"Column1": "1", "Column2": "Rod"},
    ]


def test_csv_document_multi_character_delimiter() -> None:
    assert _call("Csv.Document", "OrderID#|#Color\r\n1#|#Red", None, "#|#") == [
        {"Column1": "OrderID", "Column2": "Color"},
        {"Column1": "1", "Column2": "Red"},
    ]


def test_csv_document_named_columns() -> None:
    assert _call("Csv.Document", "1,Barb", ["ID", "Name"]) == [
        {"ID": "1", "Name": "Barb"}
    ]


def test_csv_document_short_rows_pad_with_null() -> None:
    rows = _call("Csv.Document", "1|Barb\r\n2|Cal", {"Delimiter": "|", "Columns": 3})
    assert rows == [
        {"Column1": "1", "Column2": "Barb", "Column3": None},
        {"Column1": "2", "Column2": "Cal", "Column3": None},
    ]


def test_csv_document_extra_values_error_by_default() -> None:
    with pytest.raises(EvalError, match="has 3 values but 2 column"):
        _call("Csv.Document", "a,b\r\n1,2,3", 2)


def test_csv_document_extra_values_ignore_drops_them() -> None:
    rows = _call(
        "Csv.Document",
        "a,b\r\n1,2,3",
        {"Columns": 2, "ExtraValues": "ExtraValues.Ignore"},
    )
    assert rows == [{"Column1": "a", "Column2": "b"}, {"Column1": "1", "Column2": "2"}]


def test_csv_document_quoted_field_with_embedded_delimiter() -> None:
    assert _call("Csv.Document", 'a,"b,c"') == [{"Column1": "a", "Column2": "b,c"}]


def test_csv_document_escaped_quote() -> None:
    assert _call("Csv.Document", 'a,"say ""hi"""') == [
        {"Column1": "a", "Column2": 'say "hi"'}
    ]


def test_csv_document_quoted_newline_is_data_by_default() -> None:
    # QuoteStyle.Csv (default): a newline inside quotes belongs to the value.
    assert _call("Csv.Document", 'a,"one\r\ntwo"') == [
        {"Column1": "a", "Column2": "one\r\ntwo"}
    ]


def test_csv_document_quotestyle_none_ends_the_row_at_a_newline() -> None:
    rows = _call(
        "Csv.Document",
        '1|Barb|"Smith\r\n2|Cal|Fisher',
        {"Delimiter": "|", "Columns": 3, "QuoteStyle": "QuoteStyle.None"},
    )
    assert rows == [
        {"Column1": "1", "Column2": "Barb", "Column3": "Smith"},
        {"Column1": "2", "Column2": "Cal", "Column3": "Fisher"},
    ]


def test_csv_document_trailing_newline_is_not_a_row() -> None:
    assert _call("Csv.Document", "a,b\n1,2\n") == [
        {"Column1": "a", "Column2": "b"},
        {"Column1": "1", "Column2": "2"},
    ]


def test_csv_document_whitespace_delimiter() -> None:
    assert _call("Csv.Document", "a   b\n1\t2", None, "") == [
        {"Column1": "a", "Column2": "b"},
        {"Column1": "1", "Column2": "2"},
    ]


def test_csv_document_strips_utf8_bom_from_first_header() -> None:
    # A BOM left in place becomes part of the first column name after
    # PromoteHeaders, and every later reference to that column then fails.
    #
    # Written as the \ufeff escape, never as a literal invisible character:
    # this repo runs a commit hook that strips zero-width characters from
    # source, and it silently ate the literal version - leaving a test that
    # passed while asserting nothing. The assert below makes that failure
    # loud if it ever happens again.
    data = "\ufeffOrderID,Item\r\n1,Rod".encode()
    assert data.startswith(b"\xef\xbb\xbf"), "the BOM under test went missing"
    assert _call("Csv.Document", data) == [
        {"Column1": "OrderID", "Column2": "Item"},
        {"Column1": "1", "Column2": "Rod"},
    ]


def test_csv_document_decodes_windows_1252() -> None:
    data = "Café,x".encode("cp1252")
    assert _call("Csv.Document", data, None, None, None, 1252) == [
        {"Column1": "Café", "Column2": "x"}
    ]


def test_csv_document_unknown_code_page_refuses() -> None:
    with pytest.raises(UnsupportedError, match="code page 99999"):
        _call("Csv.Document", b"a,b", None, None, None, 99999)


def test_csv_document_record_and_positional_options_conflict() -> None:
    with pytest.raises(EvalError, match="cannot be combined"):
        _call("Csv.Document", "a,b", {"Delimiter": ","}, ",")


# --- File.Contents ------------------------------------------------------


def test_file_contents_reads_bytes(tmp_path: Path) -> None:
    target = tmp_path / "x.csv"
    target.write_bytes(b"a,b\n1,2\n")
    assert _call("File.Contents", str(target)) == b"a,b\n1,2\n"


def test_file_contents_missing_path_points_at_bind(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="--bind"):
        _call("File.Contents", str(tmp_path / "nope.csv"))


def test_file_contents_directory_is_not_a_file(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="not a file"):
        _call("File.Contents", str(tmp_path))


# --- Text.FromBinary ----------------------------------------------------


def test_text_from_binary_defaults_to_utf8() -> None:
    assert _call("Text.FromBinary", "héllo".encode()) == "héllo"


def test_text_from_binary_rejects_text() -> None:
    with pytest.raises(EvalError, match="expected binary"):
        _call("Text.FromBinary", "already text")


# --- the boundary that must still refuse --------------------------------


@pytest.mark.parametrize(
    "name", ["Sql.Database", "Web.Contents", "SharePoint.Files", "Odbc.DataSource"]
)
def test_engine_backed_sources_still_refuse(name: str) -> None:
    with pytest.raises(UnsupportedError, match="connector"):
        evaluate(f'let Source = {name}("x") in Source')


# --- the whole point: a real query, end to end, with no --bind ----------


def test_real_query_runs_without_bind(tmp_path: Path) -> None:
    csv = tmp_path / "sales.csv"
    csv.write_text(
        "OrderID,Region,Amount\n1,East,100\n2,West,200\n3,East,50\n", encoding="utf-8"
    )
    source = f'''
let
    Source = Csv.Document(
        File.Contents("{csv.as_posix()}"),
        [Delimiter=",", Columns=3, Encoding=65001]
    ),
    #"Promoted Headers" = Table.PromoteHeaders(
        Source, [PromoteAllScalars=true]
    ),
    #"Changed Type" = Table.TransformColumnTypes(
        #"Promoted Headers", {{{{"Amount", Int64.Type}}}}
    ),
    #"Grouped Rows" = Table.Group(
        #"Changed Type",
        {{"Region"}},
        {{{{"Total", each List.Sum([Amount]), type number}}}}
    )
in
    #"Grouped Rows"
'''
    assert evaluate(source) == [
        {"Region": "East", "Total": 150},
        {"Region": "West", "Total": 200},
    ]
