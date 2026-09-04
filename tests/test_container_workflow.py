"""The file-in workflow: list the queries in a container, run one, preview an edit.

These cover the loop someone actually has - "here is a .pbix, what is in it and
what does it do" - rather than the individual functions underneath.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pqtools import evaluate, read_sections
from pqtools.cli import main

_SAMPLE = (
    Path(__file__).resolve().parents[1]
    / ".samples"
    / "real-powerbi-fuzzy-matching.pbix"
)
_needs_sample = pytest.mark.skipif(
    not _SAMPLE.exists(),
    reason="real .pbix sample not present (.samples is gitignored)",
)


# --- the "Enter Data" shape, which needs no sample file ------------------


def test_enter_data_shape_runs_end_to_end() -> None:
    # This is verbatim the shape Power BI writes for a manually entered table:
    # base64 -> raw deflate -> JSON rows -> a `type table [...]` column spec.
    # Every piece of it was unsupported before 0.7.0.
    source = (
        "Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("
        '"i45WMlTSUXLKzMkBUiERSrE60UpGQGY4RMTZESxiDBXJTMwFsSCCJkBmVH4qkPSLVIqNBQA=", '
        "BinaryEncoding.Base64), Compression.Deflate)), "
        'type table [ID = text, #"First Name" = text, State = text])'
    )
    assert evaluate(source) == [
        {"ID": "1", "First Name": "Bill", "State": "TX"},
        {"ID": "2", "First Name": "Will", "State": "CA"},
        {"ID": "3", "First Name": "William", "State": "WA"},
        {"ID": "4", "First Name": "Zoe", "State": "NY"},
    ]


def test_quoted_identifier_matches_a_real_column_name() -> None:
    # The bug this guards was quiet: step names round-tripped (defined and
    # referenced with the same raw #"..." text, so they matched themselves)
    # while a reference to a real column with a space did not resolve at all.
    rows = [{"First Name": "Bill", "Amount": 10}, {"First Name": "Zoe", "Amount": 20}]
    assert evaluate(
        'Table.SelectRows(T, each [#"First Name"] = "Zoe")', bindings={"T": rows}
    ) == [{"First Name": "Zoe", "Amount": 20}]


def test_quoted_identifier_unescapes_a_doubled_quote() -> None:
    assert evaluate('[#"He said ""hi""" = 1]') == {'He said "hi"': 1}


def test_binary_roundtrip() -> None:
    assert (
        evaluate('Binary.ToText(Binary.FromText("aGk=", BinaryEncoding.Base64))')
        == "aGk="
    )
    assert (
        evaluate('Binary.Length(Binary.FromText("aGk=", BinaryEncoding.Base64))') == 2
    )


def test_unregistered_compression_refuses_naming_it() -> None:
    # Compression.Brotli is not registered, so this stops at identifier
    # resolution rather than inside Binary.Decompress. Either way it refuses
    # and names the exact construct - which is the property that matters.
    from pqtools import UnsupportedError

    with pytest.raises(UnsupportedError, match="Compression.Brotli"):
        evaluate(
            'Binary.Decompress(Binary.FromText("aGk=", BinaryEncoding.Base64), '
            "Compression.Brotli)"
        )


def test_a_registered_but_unhandled_compression_kind_refuses_in_the_function() -> None:
    from pqtools import UnsupportedError

    with pytest.raises(UnsupportedError, match="Binary.Decompress kind"):
        evaluate(
            'Binary.Decompress(Binary.FromText("aGk=", BinaryEncoding.Base64), '
            "Order.Ascending)"
        )


# --- the container workflow, against the real Power BI file --------------


@_needs_sample
def test_list_names_every_query(capsys) -> None:
    assert main(["list", str(_SAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "People" in out
    assert "Sales" in out


@_needs_sample
def test_list_json_names_are_usable_as_member_arguments(capsys) -> None:
    assert main(["list", str(_SAMPLE), "--json"]) == 0
    import json

    names = {item["name"] for item in json.loads(capsys.readouterr().out)}
    assert names == {"People", "Sales"}


@_needs_sample
def test_eval_a_member_straight_out_of_the_pbix(capsys) -> None:
    assert main(["eval", str(_SAMPLE), "--member", "Sales", "--format", "csv"]) == 0
    out = capsys.readouterr().out
    assert "Sales Person" in out
    assert "Bill" in out


@_needs_sample
def test_format_previews_without_writing(capsys) -> None:
    before = _SAMPLE.read_bytes()
    assert main(["format", str(_SAMPLE)]) == 0
    assert "shared People" in capsys.readouterr().out
    assert _SAMPLE.read_bytes() == before, "preview must never modify the container"


@_needs_sample
def test_write_is_enabled_and_never_touches_the_original_without_a_backup(
    tmp_path: Path, capsys
) -> None:
    # Operates on a COPY: the sample is a real Power BI file and the point of
    # the test is that writing works, not that the fixture survives.
    target = tmp_path / "copy.pbix"
    original = _SAMPLE.read_bytes()
    target.write_bytes(original)
    # replace-source, not format: this sample's M is already formatted, so
    # `format --write` is correctly a no-op and would prove nothing about
    # whether writing works.
    new = "section Section1;\nshared Added = 1;\n"
    assert main(["replace-source", str(target), "--source", new, "--write"]) == 0
    capsys.readouterr()
    assert target.with_suffix(".pbix.bak").read_bytes() == original
    assert target.read_bytes() != original
    assert "Added" in read_sections(target)[0].source
    assert _SAMPLE.read_bytes() == original, "the sample itself must be untouched"


@_needs_sample
def test_format_write_on_already_formatted_m_is_a_no_op(tmp_path: Path, capsys) -> None:
    # Worth pinning: "nothing changed" must mean the bytes are untouched, not
    # a silent rewrite that happens to round-trip.
    target = tmp_path / "copy.pbix"
    original = _SAMPLE.read_bytes()
    target.write_bytes(original)
    assert main(["format", str(target), "--write"]) == 0
    capsys.readouterr()
    assert target.read_bytes() == original


# --- adding a query -----------------------------------------------------


def _workbook(tmp_path: Path) -> Path:
    """A minimal .xlsx shaped the way Excel writes one (UTF-16 customXml)."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_containers import _blob, _xlsx_utf16

    path = tmp_path / "book.xlsx"
    path.write_bytes(_xlsx_utf16(_blob()))
    return path


def test_add_previews_without_writing(tmp_path: Path, capsys) -> None:
    path = _workbook(tmp_path)
    before = path.read_bytes()
    assert (
        main(["add", str(path), "--name", "NewQuery", "--source", "let A = 1 in A"])
        == 0
    )
    out = capsys.readouterr().out
    assert "shared NewQuery" in out
    assert path.read_bytes() == before


def test_add_writes_the_query_into_the_workbook(tmp_path: Path, capsys) -> None:
    path = _workbook(tmp_path)
    original = path.read_bytes()
    assert (
        main(
            [
                "add",
                str(path),
                "--name",
                "NewQuery",
                "--source",
                "let A = 1 in A",
                "--write",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert path.with_suffix(".xlsx.bak").read_bytes() == original
    assert main(["list", str(path)]) == 0
    assert "NewQuery" in capsys.readouterr().out


def test_added_query_is_runnable_by_the_name_it_was_given(
    tmp_path: Path, capsys
) -> None:
    # The reason this matters: a name needing M's #"..." spelling must still
    # be addressable as the plain name the user typed.
    path = _workbook(tmp_path)
    assert (
        main(
            [
                "add",
                str(path),
                "--name",
                "Top Colors",
                "--source",
                'let S = Table.FromRows({{"Red", 3}}, {"Colour", "N"}) in S',
                "--write",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["eval", str(path), "--member", "Top Colors", "--format", "csv"]) == 0
    out = capsys.readouterr().out
    assert "Colour,N" in out
    assert "Red,3" in out


def test_added_query_written_with_a_table_literal_runs(tmp_path: Path, capsys) -> None:
    # #table is how a person actually writes a literal table in M, and it is
    # what `pq add` gets handed. Running the documented workflow on a real
    # workbook is how the gap was found: the query saved fine and then failed
    # with "unknown identifier: #table" at the point of running it, so the
    # loop looked complete right up until the last step.
    path = _workbook(tmp_path)
    source = 'let S = #table({"Colour", "N"}, {{"Blue", 5}, {"Red", 3}}) in S'
    assert (
        main(["add", str(path), "--name", "Palette", "--source", source, "--write"])
        == 0
    )
    capsys.readouterr()
    assert main(["eval", str(path), "--member", "Palette", "--format", "csv"]) == 0
    out = capsys.readouterr().out
    assert "Colour,N" in out
    assert "Blue,5" in out
    assert "Red,3" in out


def test_add_refuses_to_shadow_an_existing_query(tmp_path: Path, capsys) -> None:
    path = _workbook(tmp_path)
    assert main(["add", str(path), "--name", "Q1", "--source", "let A = 1 in A"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_add_requires_a_name_and_a_source(tmp_path: Path, capsys) -> None:
    path = _workbook(tmp_path)
    assert main(["add", str(path), "--name", "X"]) == 2
    assert "requires --name and --source" in capsys.readouterr().err
