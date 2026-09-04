"""The file-in workflow: list the queries in a container, run one, preview an edit.

These cover the loop someone actually has - "here is a .pbix, what is in it and
what does it do" - rather than the individual functions underneath.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pqtools import evaluate
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
def test_write_into_a_container_is_still_refused(capsys) -> None:
    before = _SAMPLE.read_bytes()
    assert main(["format", str(_SAMPLE), "--write"]) == 2
    assert "not enabled" in capsys.readouterr().err
    assert _SAMPLE.read_bytes() == before
