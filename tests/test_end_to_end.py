"""The whole loop the README claims, run once, against a real query.

Every other test file pins one layer. This one is the acceptance check for
`.planning/PRD-pandas-for-powerquery-2026-09-06.md` section 6.4: open a file,
read a query's source without running it, run it, get the rows into pandas,
write and re-read a parquet file, and diff two versions - in that order, on
the query a Power Query user would actually have.

The query is `tests/fixtures/realworld/01_clean_and_type/query.pq`, emitted
by Power Query's own UI, and its `expected.json` was worked out by hand
independently of this package (see that directory's README). So a green run
here means the loop produced the numbers a person calculated, not numbers
this package agrees with itself about.

Type assertions here are deliberately STRICTER than `test_realworld.py`'s,
which tolerates a date arriving as its ISO string. Tolerance is right when
the question is "did the evaluator compute the right value"; it is wrong
here, because silently turning a date into a string is exactly the failure
the export layer exists to prevent.
"""

from __future__ import annotations

import datetime as dt
import errno
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pqtools.cli import _load_binding
from pqtools.evaluate import evaluate

_SCENARIO = Path(__file__).parent / "fixtures" / "realworld" / "01_clean_and_type"
# Synthetic, and kept OUT of realworld/, whose README declares every file
# there hand-verified and emitted by Power Query's own UI.
_MISC = Path(__file__).parent / "fixtures" / "misc"

# What scenario 01's TransformColumnTypes step declares, and therefore what
# every layer downstream has to preserve. Written out rather than derived so
# that a change in either the fixture or the type map has to be made twice,
# on purpose.
_M_TYPES: dict[str, type] = {
    "OrderID": int,
    "CustomerName": str,
    "Region": str,
    "Amount": float,
    "OrderDate": dt.date,
    "AmountWithTax": float,
}


def _rows() -> list[dict[str, Any]]:
    query = (_SCENARIO / "query.pq").read_text(encoding="utf-8")
    bindings = {"Source": _load_binding(_SCENARIO / "sales.csv")}
    result = evaluate(query, bindings=bindings)
    assert isinstance(result, list)
    return result


def test_the_evaluator_hands_over_real_python_types_not_strings() -> None:
    """The precondition for everything below.

    If the evaluator already flattened the date to a string, an export that
    produced an object column would look correct while being wrong, and no
    assertion further down would catch it.
    """
    rows = _rows()
    assert rows, "scenario 01 produced no rows"
    for column, expected_type in _M_TYPES.items():
        value = rows[0][column]
        assert isinstance(value, expected_type), (
            f"{column}: evaluator returned {type(value).__name__}, "
            f"expected {expected_type.__name__}"
        )
    # bool is a subclass of int in Python; assert the int columns are not
    # secretly logicals, which `isinstance(..., int)` alone would allow.
    assert not isinstance(rows[0]["OrderID"], bool)


def test_the_rows_match_the_independently_calculated_expected_output() -> None:
    rows = _rows()
    expected = json.loads((_SCENARIO / "expected.json").read_text(encoding="utf-8"))
    assert len(rows) == len(expected)
    assert [row["OrderID"] for row in rows] == [row["OrderID"] for row in expected]
    assert rows[0]["OrderDate"].isoformat() == expected[0]["OrderDate"]
    assert rows[0]["AmountWithTax"] == pytest.approx(expected[0]["AmountWithTax"])


def test_the_loop_reaches_pandas_with_every_column_still_typed() -> None:
    pd = pytest.importorskip("pandas")
    from pqtools.export import to_pandas

    frame = to_pandas(_rows())
    assert list(frame.columns) == list(_M_TYPES)
    assert len(frame) == len(_rows())

    # The point of the whole feature: an integer column is an integer column,
    # a date column is a date column, and neither arrived as `object` because
    # something upstream stringified it.
    assert pd.api.types.is_integer_dtype(frame["OrderID"])
    assert pd.api.types.is_float_dtype(frame["Amount"])
    assert (
        pd.api.types.is_string_dtype(frame["Region"]) or frame["Region"].dtype == object
    )
    first_date = frame["OrderDate"].iloc[0]
    assert isinstance(first_date, (dt.date, pd.Timestamp)), type(first_date)


def test_the_loop_survives_a_parquet_round_trip(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    import pandas as pd

    from pqtools.export import to_parquet

    rows = _rows()
    target = tmp_path / "orders.parquet"
    to_parquet(rows, target)
    back = pd.read_parquet(target)
    assert len(back) == len(rows)
    assert list(back.columns) == list(_M_TYPES)
    assert back["OrderID"].tolist() == [row["OrderID"] for row in rows]
    # The column a round trip is most likely to flatten.
    assert str(back["OrderDate"].iloc[0])[:10] == rows[0]["OrderDate"].isoformat()


def test_export_refuses_rather_than_filling_a_missing_column() -> None:
    """Ragged rows are the silent-wrong-data case for a column-shaped export.

    pandas would happily produce NaN for the absent key, which reads as "this
    order had no region" rather than "these rows do not describe the same
    table". M has no such table, so neither does this.
    """
    from pqtools.export import ExportRefusal, to_pandas

    pytest.importorskip("pandas")
    ragged = [{"a": 1, "b": 2}, {"a": 3}]
    with pytest.raises(ExportRefusal):
        to_pandas(ragged)


def test_the_cli_writes_a_parquet_file_a_reader_can_type(tmp_path: Path) -> None:
    """`pq eval --to parquet` is the loop's last step, from a shell.

    Driven through `main()` rather than a subprocess so a failure surfaces as
    the real exception rather than an exit code with no traceback.
    """
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")

    from pqtools.cli import main

    target = tmp_path / "orders.parquet"
    code = main(
        [
            "eval",
            str(_SCENARIO / "query.pq"),
            "--bind",
            f"Source={_SCENARIO / 'sales.csv'}",
            "--to",
            "parquet",
            "--out",
            str(target),
        ]
    )
    assert code == 0
    assert target.exists()
    back = pd.read_parquet(target)
    assert list(back.columns) == list(_M_TYPES)
    assert pd.api.types.is_integer_dtype(back["OrderID"])
    assert back["AmountWithTax"].iloc[0] == pytest.approx(604.8)


def test_to_parquet_without_out_refuses_before_running_the_query(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A columnar format needs a file, and the mistake is visible up front.

    The query here reads a Windows path that does not exist, so if the
    refusal came *after* evaluation this would report File.Contents instead -
    which is how the ordering is pinned without timing anything.
    """
    from pqtools.cli import main

    assert main(["eval", str(_SCENARIO / "query.pq"), "--to", "parquet"]) != 0
    message = capsys.readouterr().err
    assert "--out" in message, message
    assert "File.Contents" not in message, message


def test_to_parquet_refuses_a_result_that_is_not_a_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from pqtools.cli import main

    code = main(
        [
            "eval",
            str(_MISC / "scalar.pq"),
            "--to",
            "parquet",
            "--out",
            str(tmp_path / "x.parquet"),
        ]
    )
    assert code != 0
    assert "requires the result to be a table" in capsys.readouterr().err
    assert not (tmp_path / "x.parquet").exists(), "refused but still wrote a file"


def test_set_param_obeys_the_same_network_gate_as_the_query(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The property that makes `--set-param` running real M acceptable.

    It evaluates an M expression, not a restricted literal grammar - so the
    thing worth pinning is that it inherits the query's IO policy rather than
    opening a side door around it.
    """
    from pqtools.cli import main

    query = tmp_path / "q.pq"
    query.write_text("let x = P in x\n", encoding="utf-8")
    code = main(
        ["eval", str(query), "--set-param", 'P=Web.Contents("http://example.com")']
    )
    assert code != 0
    assert "--allow-net" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Regressions from the round-11 review. Each of these reproduced against the
# committed tree before it was fixed; the reproduction is in the docstring
# so the test says what it is defending, not just that it passes.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--allow-net", "explain", "Table.SelectRows"],
        ["--json", "explain", "Table.SelectRows"],
        ["explain", "Table.SelectRows"],
        ["explain", "Table.SelectRows", "--json"],
    ],
)
def test_an_option_before_the_verb_does_not_change_the_answer(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-11 HIGH.

    The verb used to be read as `argv[0]`, which argparse has never required
    it to be. With any option first, `explain` received a `Path` instead of a
    name, found it in no registry, and reported a function pqtools implements
    as "not a name pqtools recognizes" - exiting 0 on a confident wrong
    answer, from the one verb whose stated guarantee is that it cannot drift
    from what the evaluator does.
    """
    from pqtools.cli import main

    assert main(argv) == 0
    assert "is implemented by pqtools" in capsys.readouterr().out


@pytest.mark.parametrize("verb", ["check", "parse", "dependencies", "list", "show"])
def test_a_batch_verb_survives_an_option_in_any_position(
    verb: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same HIGH's other half: `pq --json check f.pq` raised a bare
    `TypeError: 'PosixPath' object is not iterable` - an invocation that
    worked before the batch feature existed."""
    from pqtools.cli import main

    good = tmp_path / "a.pq"
    good.write_text("let a = 1 in a\n", encoding="utf-8")
    for argv in ([verb, "--json", str(good)], ["--json", verb, str(good)]):
        assert main(argv) == 0, argv
        capsys.readouterr()


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["explain"], "exactly one NAME"),
        (["diff", "a.pq"], "exactly two files"),
        (["eval", "a.pq", "b.pq"], "exactly one file"),
        (["check"], "at least one file"),
    ],
)
def test_wrong_argument_count_is_a_typed_refusal_not_a_traceback(
    argv: list[str], expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from pqtools.cli import main

    assert main(argv) == 2
    assert expected in capsys.readouterr().err


def test_an_unknown_option_is_still_rejected() -> None:
    # The arity fix folds argparse's leftover positionals back into `file`,
    # which must not quietly swallow a mistyped flag along with them.
    from pqtools.cli import main

    with pytest.raises(SystemExit):
        main(["list", "--bogus", "a.pq"])


def test_list_json_carries_a_read_failure_into_the_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`pq list --json` reported failures on stderr only, so stdout carried a
    complete-looking array with nothing saying a file had failed. A consumer
    reading stdout alone would have believed it was the whole answer."""
    import json as jsonlib

    from pqtools.cli import main

    good = tmp_path / "s.pq"
    good.write_text("section S;\nshared Q = 1 + 1;\n", encoding="utf-8")
    assert main(["list", "--json", str(good), str(tmp_path / "missing.pq")]) == 2
    records = jsonlib.loads(capsys.readouterr().out)
    assert any("error" in record for record in records), records


def test_a_real_file_whose_name_contains_glob_syntax_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`report[1].pq` - what a downloads folder produces - was reported as
    "no files matched glob", because `glob.glob` read the brackets as a
    character class. The file was right there."""
    from pqtools.cli import main

    bracketed = tmp_path / "report[1].pq"
    bracketed.write_text("let a = 1 in a\n", encoding="utf-8")
    assert main(["show", str(bracketed)]) == 0
    assert "let a = 1 in a" in capsys.readouterr().out


def test_the_library_refuses_a_non_table_the_way_the_cli_does() -> None:
    """README's own example is `to_pandas(report.eval("Sales"))`, and a query
    returning a scalar or a record is ordinary. That used to raise
    `TypeError: 'int' object is not subscriptable` from inside the column
    builder, where the module documents a typed `ExportRefusal`."""
    from pqtools.export import ExportRefusal, to_arrow, to_pandas

    for export in (to_pandas, to_arrow):
        for value in (5, "text", {"a": 1}, [1, 2]):
            with pytest.raises(ExportRefusal, match="table"):
                export(value)


def test_the_member_expression_parser_has_exactly_one_definition() -> None:
    """It was copied into cli.py and export.py while the two lanes were built
    in parallel. Two copies of a parser-driven span calculation drift; this
    pins that they are the same object, not two that currently agree."""
    from pqtools import cli, containers, export

    assert cli._member_expression is containers.member_expression
    assert export._member_expression is containers.member_expression
    assert cli._is_container is containers.is_container


def test_the_handle_and_the_cli_agree_on_what_a_query_source_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import pqtools
    from pqtools.cli import main

    path = tmp_path / "s.pq"
    path.write_text("section S;\nshared Q = 1 + 1;\n", encoding="utf-8")
    assert main(["show", str(path), "--member", "Q"]) == 0
    from_cli = capsys.readouterr().out.strip()
    assert pqtools.open(path).source("Q") == from_cli == "1 + 1"


def test_the_handle_can_run_a_query_that_needs_its_data_bound() -> None:
    """`.eval` took no bindings and no io policy, so it was pinned to
    DENY_ALL and could only run a query that reads nothing - not the query
    anyone opens a report to find."""
    import pqtools
    from pqtools.cli import _load_binding

    handle = pqtools.open(_SCENARIO / "query.pq")
    rows = handle.eval(
        handle.queries[0],
        bindings={"Source": _load_binding(_SCENARIO / "sales.csv")},
    )
    assert len(rows) == 5
    assert rows[0]["OrderID"] == 1005


# --------------------------------------------------------------------------
# Regressions from the round-12 review - the review OF the round-11 fixes.
# The first two are defects those fixes introduced.
# --------------------------------------------------------------------------


def test_plain_text_list_survives_a_read_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-12 HIGH, and a defect the round-11 fix introduced.

    Carrying read failures into `pq list --json` was right; appending them to
    the same list the PLAIN-TEXT branch formats with `item["name"]` was not.
    Every non-JSON `pq list` with an unreadable file died on `KeyError:
    'name'`. The test written alongside that fix covered only the `--json`
    path - the one that already worked - which is how it shipped.
    """
    from pqtools.cli import main

    good = tmp_path / "s.pq"
    good.write_text("section S;\nshared Q = 1 + 1;\n", encoding="utf-8")
    assert main(["list", str(good), str(tmp_path / "missing.pq")]) == 2
    captured = capsys.readouterr()
    assert "Q" in captured.out, "the names it did find must still print"
    assert "missing.pq" in captured.err


def test_a_column_mixing_tzinfo_objects_at_one_offset_exports(tmp_path: Path) -> None:
    """`_classify_column` admits a datetimezone column on equal `utcoffset()`,
    not equal `tzinfo`, so UTC and Europe/London in January are one column.
    Reading the offset off the first row's `tzinfo` made pandas infer
    `object`, and `.dt` then raised where the module promises a refusal -
    while `to_arrow` on the same rows succeeded, because it had always
    derived the offset from `utcoffset()`.
    """
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from zoneinfo import ZoneInfo

    from pqtools.export import to_arrow, to_pandas

    rows = [
        {"a": dt.datetime(2024, 1, 1, tzinfo=dt.UTC)},
        {"a": dt.datetime(2024, 1, 2, tzinfo=ZoneInfo("Europe/London"))},
    ]
    assert str(to_pandas(rows)["a"].dtype) == "datetime64[us, UTC]"
    assert "tz=+00:00" in str(to_arrow(rows).schema.field("a").type)


def test_a_missing_file_under_a_read_verb_is_an_io_error_not_a_write_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`core._snapshot` is the READ path as well as the write path, and it
    reported every failed open as `M_SAFE_WRITE_REFUSED: writes require a
    regular, non-symlink, single-link file`. For `pq check missing.pq` that
    is not true of anything the user did. The genuine write-safety refusals
    (symlink, non-regular, multiple hard links) keep that code.
    """
    from pqtools.cli import main

    assert main(["check", str(tmp_path / "missing.pq")]) == 2
    err = capsys.readouterr().err
    assert "M_IO_ERROR" in err, err
    assert "writes require" not in err, err


# ---------------------------------------------------------------------------
# Round-13 review regressions
# ---------------------------------------------------------------------------


def test_a_non_utc_offset_column_keeps_its_dtype_and_its_instants() -> None:
    """The round-13 MEDIUM: the tz regression test picked the one offset that
    skips the code it was written for.

    `str(dt.UTC)` is `"UTC"`, so a UTC-only test never builds the interpolated
    `datetime64[us, UTC+05:30]` form the round-12 rewrite introduced - the
    half whose parsing could plausibly differ across `pandas>=2`. It also
    asserted only `.dtype`, so a column that silently shifted every instant
    would still have passed.

    (The review's companion claim - that the interpolated form fails on the
    pandas 2.x floor - is FALSE, and this test is what says so: it runs on
    both ends of the declared range. Measured on 2.3.3 and 3.0.5, identical.)
    """
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")

    from pqtools.export import to_arrow, to_pandas

    kolkata = dt.timezone(dt.timedelta(hours=5, minutes=30))
    pacific = dt.timezone(dt.timedelta(hours=-8))

    for offset, label in ((kolkata, "UTC+05:30"), (pacific, "UTC-08:00")):
        moment = dt.datetime(2024, 3, 1, 12, 0, tzinfo=offset)
        rows = [{"a": moment}, {"a": None}]
        column = to_pandas(rows)["a"]
        assert str(column.dtype) == f"datetime64[us, {label}]", column.dtype
        # The instant, not just the type. A wrong offset keeps the dtype.
        assert column.iloc[0].to_pydatetime() == moment
        assert column.iloc[0].utcoffset() == offset.utcoffset(None)
        assert column.isna().iloc[1]
        # Arrow derives the offset the same way and must agree.
        assert str(to_arrow(rows).schema.field("a").type.tz) == label[3:]


def test_list_json_keeps_failures_in_argument_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-13 LOW: fixing the plain-text `KeyError` by moving failures
    into a second list appended them all AFTER the successes, so a consumer
    could no longer align `pq list --json` records with the files it passed.
    """
    from pqtools.cli import main

    good = tmp_path / "a.pq"
    good.write_text("section S;\nshared A = 1;\n", encoding="utf-8")
    later = tmp_path / "c.pq"
    later.write_text("section S;\nshared C = 3;\n", encoding="utf-8")
    missing = tmp_path / "b.pq"

    assert main(["list", str(good), str(missing), str(later), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert [item.get("name") or item["file"] for item in payload] == [
        "A",
        str(missing),
        "C",
    ], payload


def test_list_does_not_call_an_unreadable_file_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-13 LOW, second half: with every file failing, the text path
    printed the per-file error AND `no queries found` - which says the file
    was empty, when it was unreadable.
    """
    from pqtools.cli import main

    assert main(["list", str(tmp_path / "missing.pq")]) == 2
    err = capsys.readouterr().err
    assert "M_IO_ERROR" in err, err
    assert "no queries found" not in err, err

    # ...but a genuinely empty section document still says exactly that.
    empty = tmp_path / "empty.pq"
    empty.write_text("section S;\n", encoding="utf-8")
    assert main(["list", str(empty)]) == 0
    assert "no queries found" in capsys.readouterr().err


def test_list_refuses_a_section_it_cannot_parse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Found while reproducing the round-13 LOWs, and not in the review.

    `_run_list` caught the split failure and set `members = {}` under a
    comment claiming the section was "still worth reporting". So `pq list`
    on a file that `pq check` rejects with M_PARSE_ERROR printed "no queries
    found" and exited 0 - a shorter answer than the truth, with a success
    code. That is the one failure mode the package's stated contract rules
    out entirely.
    """
    from pqtools.cli import main

    broken = tmp_path / "parsefail.pq"
    broken.write_text("section S;\nshared Broken = ( ;\n", encoding="utf-8")

    assert main(["check", str(broken)]) == 2, "precondition: check rejects it"
    assert main(["list", str(broken)]) == 2, "so list must not call it empty"
    err = capsys.readouterr().err
    assert "no queries found" not in err, err
    # The code a user actually sees. `split_shared` wraps the parse failure
    # as ContainerError, so it is M_CONTAINER_ERROR, not M_PARSE_ERROR - the
    # round-13 comment and closeout both said otherwise and no assertion
    # pinned it.
    assert "M_CONTAINER_ERROR" in err, err


def test_a_symlink_swapped_in_after_lstat_is_a_write_refusal(
    tmp_path: Path,
) -> None:
    """The round-13 LOW: `O_NOFOLLOW` firing means the path became a symlink
    between `lstat` and `os.open` - the TOCTOU race the flag closes. It is a
    write-safety refusal, but it escaped as a bare `OSError(ELOOP)` and
    rendered as `M_IO_ERROR: Too many levels of symbolic links`.
    """
    from unittest import mock

    from pqtools.core import SafeWriteError, _snapshot

    target = tmp_path / "real.pq"
    target.write_text("let a = 1 in a\n", encoding="utf-8")
    link = tmp_path / "link.pq"
    link.symlink_to(target)

    real_lstat = os.lstat
    seen: list[str] = []

    # Model the race, do not just disable the check. The FIRST lstat is the
    # one `_snapshot` makes before opening, and it must see a regular file -
    # that is what makes O_NOFOLLOW's ELOOP a surprise. Every later lstat
    # happens AFTER the swap, so it must see reality: a symlink. An earlier
    # version of this test returned `target`'s stat unconditionally, which
    # modelled a swap that then un-swapped itself, and it failed the moment
    # the code started asking the filesystem which kind of ELOOP it had.
    #
    # Discriminating on the path also matters: `pqtools.core.os` IS the
    # stdlib module, so this patch is process-wide while it is installed.
    def racing_lstat(candidate, *args, **kwargs):  # type: ignore[no-untyped-def]
        # isinstance-guarded: the patch is on stdlib `os` while installed,
        # and `Path()` raises TypeError on a bytes path or an int fd, which
        # would surface as an unrelated error rather than a test failure.
        if isinstance(candidate, (str, os.PathLike)) and Path(candidate) == link:
            seen.append(str(candidate))
            if len(seen) == 1:
                return real_lstat(target)  # pre-swap: a regular file
        return real_lstat(candidate, *args, **kwargs)

    with mock.patch("pqtools.core.os.lstat", racing_lstat):
        with pytest.raises(SafeWriteError, match="regular, non-symlink"):
            _snapshot(link)
    assert seen, "the pre-open lstat must have happened"

    # A plain missing file is still an OSError, not a write refusal.
    with pytest.raises(OSError):
        _snapshot(tmp_path / "nope.pq")


# ---------------------------------------------------------------------------
# Round-14 review regressions
# ---------------------------------------------------------------------------


def test_a_symlink_loop_in_a_directory_component_is_an_io_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-14 HIGH, and a regression round 13 introduced.

    `os.lstat` raises ELOOP on its own when a DIRECTORY component of the path
    is a symlink loop - no `O_NOFOLLOW`, no TOCTOU race. Round 13 wrapped
    ELOOP for the whole `try`, which caught that case too, so `pq check` - a
    READ verb, on an ordinary broken path - printed `M_SAFE_WRITE_REFUSED:
    writes require a regular, non-symlink, single-link file`. That is exactly
    the false-message class round 12 removed, on a case far more reachable
    than the race the wrap was aimed at, and it contradicted the README text
    the same commit added.
    """
    from pqtools.cli import main

    loop = tmp_path / "loopdir"
    loop.symlink_to(loop)  # resolving anything THROUGH it is ELOOP

    assert main(["check", str(loop / "x.pq")]) == 2
    err = capsys.readouterr().err
    assert "M_IO_ERROR" in err, err
    assert "writes require" not in err, err


def test_list_json_distinguishes_two_containers_that_both_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-14 MEDIUM: `read_sections` hardcodes
    `path="Formulas/Section1.m"` for every .xlsx/.pbix, so keying the failure
    record on `section.path` gave two different unparseable workbooks the
    identical `"file"` value - defeating the argument-order alignment the
    record exists to provide.
    """
    import base64
    import io
    import struct
    import zipfile

    from pqtools.cli import main

    def workbook(path: Path, m_text: str) -> None:
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as archive:
            archive.writestr("Formulas/Section1.m", m_text)
        out = [struct.pack("<I", 0)]
        for segment in (
            inner.getvalue(),
            b"\xef\xbb\xbf<permissions/>",
            b"\x00\x00\x00\x00\xef\xbb\xbf<metadata/>",
            b"\x01\x02\x03\x04binding",
        ):
            out.append(struct.pack("<I", len(segment)))
            out.append(segment)
        xml = (
            b'<?xml version="1.0"?><DataMashup xmlns="x">'
            + base64.b64encode(b"".join(out))
            + b"</DataMashup>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/workbook.xml", "<workbook/>")
            archive.writestr("customXml/item1.xml", xml)
        path.write_bytes(buffer.getvalue())

    first, second = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    workbook(first, "section S; shared BrokenA = ( ;")
    workbook(second, "section S; shared BrokenB = ) ;")

    assert main(["list", str(first), str(second), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    files = [item["file"] for item in payload]
    assert len(set(files)) == 2, f"both records claim the same file: {files}"
    assert files[0].startswith(str(first)), files
    assert files[1].startswith(str(second)), files


def test_a_plain_file_failure_is_not_qualified_against_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The container-qualified form is for container sections only. A plain
    .pq gets a synthetic section whose container IS its path, so applying it
    unconditionally would print `q.pq!q.pq`.
    """
    from pqtools.cli import main

    broken = tmp_path / "q.pq"
    broken.write_text("section S;\nshared Broken = ( ;\n", encoding="utf-8")
    assert main(["list", str(broken)]) == 2
    err = capsys.readouterr().err
    assert f"{broken}!{broken}" not in err, err
    assert str(broken) in err, err


def test_a_write_to_an_unresolvable_path_is_an_io_error_not_a_lock_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Found by testing the case a review raised and I had declined.

    `_atomic_write` turned every `OSError` from opening the lock file into
    `M_SAFE_WRITE_REFUSED: unable to acquire safe source lock`. For a path
    that cannot resolve - a component that is a regular file, or a missing
    parent - that describes a contended lock which was never contended, while
    `pq check` on the identical path correctly said "Not a directory". Same
    defect class as the `_snapshot` thread: a true refusal with a false
    reason.
    """
    from pqtools.cli import main

    real = tmp_path / "real.pq"
    real.write_text("let a = 1 in a\n", encoding="utf-8")
    through_a_file = real / "child.pq"

    assert main(["check", str(through_a_file)]) == 2
    read_err = capsys.readouterr().err
    assert "M_IO_ERROR" in read_err, read_err

    assert main(["format", str(through_a_file), "--write"]) == 2
    write_err = capsys.readouterr().err
    assert "M_IO_ERROR" in write_err, write_err
    assert "acquire safe source lock" not in write_err, write_err


def test_a_symlinked_lock_file_is_still_a_write_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half: `O_NOFOLLOW` rejecting a symlinked lock file IS a
    refusal to take the lock, and must keep the typed code. Without this the
    fix above could be "widened" into dropping the lock refusal entirely.
    """
    from pqtools.cli import main

    query = tmp_path / "q.pq"
    query.write_text("let a  =  1 in a\n", encoding="utf-8")
    (tmp_path / ".q.pq.lock").symlink_to(tmp_path / "elsewhere")

    assert main(["format", str(query), "--write"]) == 2
    err = capsys.readouterr().err
    assert "M_SAFE_WRITE_REFUSED" in err, err
    # The precise message, not the old catch-all. Opening the lock cannot
    # report contention - flock does - so "unable to acquire safe source
    # lock" was never the right thing to say about a symlinked lock file.
    assert "source lock must be a regular single-link file" in err, err


# ---------------------------------------------------------------------------
# The filesystem errno taxonomy, as one table
# ---------------------------------------------------------------------------


def _fs_case(tmp_path: Path, kind: str) -> Path:
    """Build one filesystem shape and return the path to act on."""
    root = tmp_path / kind
    root.mkdir()
    if kind == "missing":
        return root / "nope.pq"
    if kind == "loop_in_directory":
        loop = root / "loopdir"
        loop.symlink_to(loop)
        return loop / "x.pq"
    if kind == "through_a_regular_file":
        real = root / "real.pq"
        real.write_text("let a  =  1 in a\n", encoding="utf-8")
        return real / "child.pq"
    query = root / "q.pq"
    query.write_text("let a  =  1 in a\n", encoding="utf-8")
    if kind == "target_is_a_symlink":
        link = root / "link.pq"
        link.symlink_to(query)
        return link
    if kind == "target_is_hard_linked":
        os.link(query, root / "second.pq")
        return query
    if kind == "lock_is_a_symlink":
        (root / ".q.pq.lock").symlink_to(root / "elsewhere")
        return query
    raise AssertionError(f"unknown case {kind}")


# (shape, code from a READ verb, code from a WRITE verb).
#
# The point of the table is the FIRST TWO COLUMNS AGREEING wherever the
# problem is the path rather than the write. Rounds 12-14 each broke one cell
# here, in a different place each time, and each fix was written without a
# test that could see the other cells.
_FS_TAXONOMY = [
    ("missing", "M_IO_ERROR", "M_IO_ERROR"),
    ("loop_in_directory", "M_IO_ERROR", "M_IO_ERROR"),
    ("through_a_regular_file", "M_IO_ERROR", "M_IO_ERROR"),
    ("target_is_a_symlink", "M_SAFE_WRITE_REFUSED", "M_SAFE_WRITE_REFUSED"),
    ("target_is_hard_linked", "M_SAFE_WRITE_REFUSED", "M_SAFE_WRITE_REFUSED"),
    # Only the write path takes a lock, so the read verb succeeds here.
    ("lock_is_a_symlink", None, "M_SAFE_WRITE_REFUSED"),
]


@pytest.mark.parametrize(("kind", "read_code", "write_code"), _FS_TAXONOMY)
def test_the_filesystem_errno_taxonomy_holds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    read_code: str | None,
    write_code: str,
) -> None:
    """One table pinning every cell of `core._WRITE_SAFETY` end to end.

    A path problem must read as `M_IO_ERROR` from BOTH a read verb and a
    write verb - they are the same fact about the same path, and any round
    that makes them disagree has reintroduced the bug this taxonomy exists to
    prevent. Only a genuine write-safety property may differ between them.
    """
    from pqtools.cli import main

    target = _fs_case(tmp_path, kind)

    if read_code is None:
        assert main(["check", str(target)]) == 0
        capsys.readouterr()
    else:
        assert main(["check", str(target)]) == 2
        err = capsys.readouterr().err
        assert read_code in err, f"read verb: {err}"

    assert main(["format", str(target), "--write"]) == 2
    err = capsys.readouterr().err
    assert write_code in err, f"write verb: {err}"


def test_eloop_is_asked_about_rather_than_assumed() -> None:
    """The unit-level statement of the same thing.

    `os.open` raises ELOOP both when O_NOFOLLOW refuses a symlinked final
    component and when a DIRECTORY component is a loop. The table alone
    cannot tell those apart, so `_write_refusal` consults the filesystem.
    """
    from pqtools.core import UNSAFE_TO_WRITE, _FsCall, _write_refusal

    eloop = OSError(errno.ELOOP, "Too many levels of symbolic links")
    enoent = OSError(errno.ENOENT, "No such file or directory")
    absent = Path("/nonexistent/loop/x.pq")

    # os.lstat can never report a write-safety problem, whatever the errno -
    # it has no table entry at all.
    assert _write_refusal(eloop, _FsCall.RESOLVE, absent) is None

    # OPEN_LOCK has no preceding lstat, so ELOOP there is ambiguous and IS
    # asked about. This path is not a symlink, so it is an I/O fact.
    assert _write_refusal(eloop, _FsCall.OPEN_LOCK, absent) is None

    # OPEN_SOURCE is NOT asked about, deliberately: `_snapshot` lstats the
    # path immediately before, and that lstat succeeding already proves no
    # directory component is a loop. Re-checking degraded the genuine TOCTOU
    # refusal whenever the symlink was swapped back out.
    assert _write_refusal(eloop, _FsCall.OPEN_SOURCE, absent) == UNSAFE_TO_WRITE

    # An errno with no entry propagates regardless of the call.
    assert _write_refusal(enoent, _FsCall.OPEN_SOURCE, absent) is None
    assert _write_refusal(enoent, _FsCall.OPEN_LOCK, absent) is None


def test_the_toctou_refusal_survives_the_symlink_being_swapped_back(
    tmp_path: Path,
) -> None:
    """The round-16 MEDIUM, and a regression the round-15 rewrite introduced.

    Answering "was this ELOOP O_NOFOLLOW?" by lstat-ing again, AFTER
    `os.open` has already failed, re-opens a window inside the very race the
    flag exists to close: an attacker who swaps the symlink in, lets the open
    fail, then swaps it back out makes the second lstat report a regular file
    and the genuine refusal degrades to `M_IO_ERROR`.

    `_snapshot` lstats the path immediately before opening it, and that lstat
    succeeding already proves no directory component is a loop - so the
    re-check was redundant there as well as harmful. It now applies only to
    the lock file, which has no preceding lstat.
    """
    from unittest import mock

    from pqtools.core import SafeWriteError, _snapshot

    target = tmp_path / "real.pq"
    target.write_text("let a = 1 in a\n", encoding="utf-8")
    query = tmp_path / "q.pq"
    query.write_text("let a = 1 in a\n", encoding="utf-8")

    real_lstat, real_open = os.lstat, os.open

    def swapped_back(candidate, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Every lstat sees a regular file: the symlink is gone again by the
        # time anyone looks. Only os.open ever witnessed it.
        if isinstance(candidate, (str, os.PathLike)) and Path(candidate) == query:
            return real_lstat(target)
        return real_lstat(candidate, *args, **kwargs)

    def refusing_open(candidate, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(candidate, (str, os.PathLike)) and Path(candidate) == query:
            raise OSError(errno.ELOOP, "Too many levels of symbolic links")
        return real_open(candidate, *args, **kwargs)

    with (
        mock.patch("pqtools.core.os.lstat", swapped_back),
        mock.patch("pqtools.core.os.open", refusing_open),
    ):
        with pytest.raises(SafeWriteError, match="regular, non-symlink"):
            _snapshot(query)


# The round-18 LOW: these fixtures ran git with the DEVELOPER'S global config
# inherited. A global `core.hooksPath` (this estate installs commit hooks across
# ~105 repos), `commit.gpgsign`, or `init.templateDir` would either fail the
# check=True commits or run estate hooks inside a throwaway fixture. None are
# set on this machine, so it was latent fragility rather than a live failure -
# but a test whose result depends on who is running it is not a control.
#
# The round-19 LOW: pinning the CONFIG was not enough, because git also takes
# its LOCATION from the environment. With `GIT_DIR` set, `git rev-parse
# --git-dir` run inside the fixture directory resolves to the caller's repo
# (reproduced) - so `git init` / `add` / `commit` would operate on the caller's
# repository and index instead of tmp_path. Every git hook runs with GIT_DIR
# and GIT_INDEX_FILE exported, and `git -c x=y` exports
# GIT_CONFIG_PARAMETERS, so "run the suite from a hook" is enough to hit it.
_GIT_LEAKS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_TEMPLATE_DIR",
    "GIT_CONFIG_PARAMETERS",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)
_GIT_ENV = {
    **{k: v for k, v in os.environ.items() if k not in _GIT_LEAKS},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(args: list[str], cwd: Path, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    return result.stdout


def _provenance(cwd: Path, python: str = "") -> str:
    """Run the real provenance script in `cwd` and return what it printed."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "gate_provenance.sh"
    result = subprocess.run(
        ["bash", str(script), python],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=_GIT_ENV,
    )
    return result.stdout


def _repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "."], path)
    _git(["config", "user.email", "t@example.invalid"], path)
    _git(["config", "user.name", "t"], path)


def _content_id(header: str) -> str:
    """The object id from the header's `# content:` line, or ''."""
    for line in header.splitlines():
        if line.startswith("# content:"):
            match = re.search(r"\b[0-9a-f]{40}\b", line)
            return match.group(0) if match else ""
    return ""


def test_the_gate_header_reports_a_staged_only_tree_as_dirty(tmp_path: Path) -> None:
    """The round-17 MEDIUM: the previous version of this test GREPPED the
    script for three substrings instead of running it.

    Inverting the clean/dirty branches - so every clean tree reported DIRTY
    and every dirty tree reported clean, i.e. the header exactly backwards -
    left that test green, because all three substrings were still present.
    The closeout listed it as one of "three behavioural" controls. It was not
    one, and this is the replacement that actually executes the thing.
    """
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)
    _git(["commit", "-qm", "init"], repo)

    clean = _provenance(repo)
    assert "(working tree DIRTY)" not in clean, clean

    # Staged only: the worktree matches the index, the index differs from
    # HEAD. This is the normal shape when gating just before a commit, and it
    # is what `git diff --quiet` called clean.
    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)
    staged = _provenance(repo)
    assert "(working tree DIRTY)" in staged, staged

    # And an untracked file alone counts too.
    _git(["restore", "--staged", "f.txt"], repo)
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    assert "(working tree DIRTY)" not in _provenance(repo)
    (repo / "untracked.txt").write_text("x\n", encoding="utf-8")
    assert "(working tree DIRTY)" in _provenance(repo)


def test_a_dirty_gate_header_still_identifies_the_tree_that_ran(
    tmp_path: Path,
) -> None:
    """The round-17 MEDIUM: a DIRTY log's SHA identifies neither the tree that
    ran nor the tree being shipped.

    Not hypothetical - `evidence/release-gate-2026-09-07-round16-fixes.log`
    names `89367cd`, an autocommit holding two evidence files, while every
    change it certifies sat uncommitted in the worktree.

    The round-18 HIGH is that the FIX for that had the same hole, and the
    version of this test written alongside it could not see it: it asserted the
    line held a 40-hex string that was not "unknown", never resolved it, and
    exercised only a tracked modification - the one shape `git stash create`
    does capture. So it stayed green while the round-17 gate log certified
    295b580c, a tree that does not contain the script that printed the line.
    This version resolves the id and reads the blobs out of it.
    """
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)
    _git(["commit", "-qm", "init"], repo)
    assert "# content:" not in _provenance(repo), "clean trees need no content line"

    # A tracked modification AND an untracked file - the second is what the
    # header counts as dirty and what `git stash create` silently dropped.
    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("only-in-the-worktree\n", encoding="utf-8")
    dirty = _provenance(repo)
    assert "# content:" in dirty, dirty
    identity = _content_id(dirty)
    assert identity, dirty

    # Resolve it. An id that does not name a readable tree is not an identity.
    listing = _git(["ls-tree", "-r", identity], repo)
    paths = {line.split("\t", 1)[1] for line in listing.splitlines() if "\t" in line}
    assert "untracked.txt" in paths, listing
    assert "f.txt" in paths, listing

    # And the CONTENT is the new content, not HEAD's.
    blobs = {
        line.split("\t", 1)[1]: line.split()[2]
        for line in listing.splitlines()
        if "\t" in line
    }
    assert blobs["f.txt"] == _git(["hash-object", "f.txt"], repo).strip(), listing
    assert (
        blobs["untracked.txt"] == _git(["hash-object", "untracked.txt"], repo).strip()
    ), listing


def test_the_gate_header_names_a_detached_head_by_rev(tmp_path: Path) -> None:
    """The round-18 LOW: the detached fallback was `rev-parse --abbrev-ref`,
    which prints the literal string "HEAD" when detached.

    Gate runs happen in detached worktrees (`mq-gate-wt-*`), so the branch
    field was uninformative in exactly the case the fallback existed for.
    """
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)
    _git(["commit", "-qm", "init"], repo)
    _git(["checkout", "-q", "--detach"], repo)

    header = _provenance(repo)
    branch = [ln for ln in header.splitlines() if ln.startswith("# branch:")][0]
    assert branch.split(":", 1)[1].strip() != "HEAD", branch
    short = _git(["rev-parse", "--short", "HEAD"], repo).strip()
    assert short in branch, (branch, short)


def test_the_gate_refuses_to_run_without_a_provenance_header(tmp_path: Path) -> None:
    """The round-18 MEDIUM: `bash scripts/gate_provenance.sh "$PY"` ignored its
    exit status.

    With the script renamed or absent the error went to stderr and the gate ran
    on to `GATE PASSED` with exit 0 and a header-less log - an unprovenanced
    pass that reads exactly like a provenanced one. Extracting the header into
    its own file created this failure mode; nothing referenced release_gate.sh
    from the suite, so nothing could see it.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    real = Path(__file__).resolve().parent.parent / "scripts" / "release_gate.sh"
    (scripts / "release_gate.sh").write_text(
        real.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # gate_provenance.sh is deliberately NOT copied.

    result = subprocess.run(
        ["bash", "scripts/release_gate.sh"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=_GIT_ENV,
    )
    assert result.returncode != 0, result.stdout
    assert "GATE PASSED" not in result.stdout, result.stdout
    assert "provenance header failed" in result.stdout, result.stdout


def test_the_gate_header_survives_a_repo_with_no_commits(tmp_path: Path) -> None:
    """The round-17 LOW: `git diff --quiet HEAD` exits 128 on an unborn HEAD,
    so the header called an empty repo dirty. `git status --porcelain` alone
    already covers staged, unstaged and untracked, and is correct here.
    """
    repo = tmp_path / "repo"
    _repo(repo)
    header = _provenance(repo)
    assert "(working tree DIRTY)" not in header, header
    assert "# commit:  unknown" in header, header


def test_a_failed_collection_is_named_not_blanked(tmp_path: Path) -> None:
    """The other round-16 LOW, split into its own test - it was previously
    asserted inside the staged-tree test, so a failure reported under an
    unrelated name.
    """
    repo = tmp_path / "repo"
    _repo(repo)
    header = _provenance(repo, python="/nonexistent/python")
    assert "# collect: unknown - COLLECTION FAILED" in header, header


def test_the_size_cap_is_reported_by_read_verbs_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-17 MEDIUM: the sentence written to fix the read/write
    contradiction repeated it.

    Both docs listed "the input exceeds 10 MiB" among the `--write`-only
    cases, but the size check lives in `_snapshot`, which is the read path
    too - so `pq check` on an oversized file exits 2 with
    `M_SAFE_WRITE_REFUSED`. This test is what makes that sentence checkable
    rather than merely re-asserted.
    """
    from pqtools.core import MAX_BYTES

    oversized = tmp_path / "big.pq"
    oversized.write_text("let a = 1 in a\n" + "// pad\n" * ((MAX_BYTES // 7) + 16))
    assert oversized.stat().st_size > MAX_BYTES

    from pqtools.cli import main

    assert main(["check", str(oversized)]) == 2
    err = capsys.readouterr().err
    assert "M_SAFE_WRITE_REFUSED" in err, err
    assert "10 MiB" in err, err

    # ...while invalid UTF-8 really IS write-only: a read verb gets a bare
    # decode error and reports M_IO_ERROR. That is the half of the sentence
    # that was right, and it needs pinning too or the correction could drift
    # back the other way.
    invalid = tmp_path / "bad.pq"
    invalid.write_bytes(b"let a = \xff\xfe in a\n")
    assert main(["check", str(invalid)]) == 2
    read_err = capsys.readouterr().err
    assert "M_IO_ERROR" in read_err, read_err
    assert main(["format", str(invalid), "--write"]) == 2
    write_err = capsys.readouterr().err
    assert "M_SAFE_WRITE_REFUSED" in write_err, write_err


def test_the_fixture_repos_ignore_an_inherited_git_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round-19 LOW: `_GIT_ENV` pinned the config but not the LOCATION.

    With `GIT_DIR` exported - which is true inside every git hook - git run in
    the fixture directory resolves to the CALLER's repository, so the fixture
    would build its history in someone else's repo. Verified directly: with
    `GIT_DIR` set, `git rev-parse --git-dir` inside a fresh directory returns
    the host repo's path.
    """
    host = tmp_path / "host"
    _repo(host)
    (host / "h.txt").write_text("host\n", encoding="utf-8")
    _git(["add", "h.txt"], host)
    _git(["commit", "-qm", "host"], host)
    host_head = _git(["rev-parse", "HEAD"], host).strip()

    # Exactly what a git hook exports.
    monkeypatch.setenv("GIT_DIR", str(host / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(host / ".git" / "index"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath=/nonexistent'")

    # _GIT_ENV is built at import time, so rebuild it the way the module does
    # to prove the CONSTRUCTION drops these, not that they happened to be unset.
    env = {
        **{k: v for k, v in os.environ.items() if k not in _GIT_LEAKS},
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    for leaked in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS"):
        assert leaked in os.environ, leaked
        assert leaked not in env, leaked

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=fixture, check=True, env=env)
    (fixture / "f.txt").write_text("fixture\n", encoding="utf-8")
    for command in (["add", "f.txt"], ["commit", "-qm", "fixture"]):
        subprocess.run(["git", *command], cwd=fixture, check=True, env=env)

    own = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=fixture,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    assert own != host_head, "the fixture committed into the host repository"
    assert (fixture / ".git").is_dir()


def test_an_unreadable_directory_makes_the_header_refuse(tmp_path: Path) -> None:
    """The round-19 MEDIUM: `git add -A` DROPS PATHS WHILE EXITING 0.

    An unreadable directory makes it print `warning: could not open directory`
    to stderr and return success, and the tree it then writes is byte-identical
    to one where the directory never existed (reproduced side by side). An
    exit-status check cannot see this, so the script reads stderr.
    """
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)
    _git(["commit", "-qm", "init"], repo)
    # Dirty it independently: an unreadable directory is invisible to
    # `git status --porcelain` too, so on its own it would not even be DIRTY.
    (repo / "marker.txt").write_text("dirty\n", encoding="utf-8")

    locked = repo / "locked"
    locked.mkdir()
    (locked / "inside.txt").write_text("content\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        script = (
            Path(__file__).resolve().parent.parent / "scripts" / "gate_provenance.sh"
        )
        result = subprocess.run(
            ["bash", str(script)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            env=_GIT_ENV,
        )
    finally:
        locked.chmod(0o755)

    assert "COULD NOT IDENTIFY THE TREE THAT RAN" in result.stdout, result.stdout
    assert "could not open directory" in result.stdout, result.stdout
    # And it must reach the caller, not just the log.
    assert result.returncode != 0, result.stdout
    # The rest of the header is still printed - a partial header is evidence.
    assert "# commit:" in result.stdout, result.stdout
    assert "# date:" in result.stdout, result.stdout


def test_a_nested_repository_makes_the_header_refuse(tmp_path: Path) -> None:
    """The round-19 MEDIUM, second case: a nested repo becomes a `160000
    commit` gitlink whose object lives in the OTHER repo's store.

    `git status --porcelain` counts it dirty while the identity cannot be read
    back - the marker and the identity disagreeing, which is exactly what the
    content line exists to prevent. Realistic here: gate runs happen inside
    `git worktree` directories (`mq-gate-wt-*`).
    """
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)
    _git(["commit", "-qm", "init"], repo)

    nested = repo / "nested"
    _repo(nested)
    (nested / "n.txt").write_text("inner\n", encoding="utf-8")
    _git(["add", "n.txt"], nested)
    _git(["commit", "-qm", "inner"], nested)

    assert "nested" in _git(["status", "--porcelain"], repo), "precondition: dirty"

    script = Path(__file__).resolve().parent.parent / "scripts" / "gate_provenance.sh"
    result = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_GIT_ENV,
    )
    assert "COULD NOT IDENTIFY THE TREE THAT RAN" in result.stdout, result.stdout
    assert result.returncode != 0, result.stdout


def test_a_failed_collection_reaches_the_caller(tmp_path: Path) -> None:
    """The round-19 MEDIUM: the script had no `exit` in it at all.

    Its last command was `printf '# date: …'`, so it always returned 0. Both
    `unknown` branches printed their refusal and the gate ran on to
    `GATE PASSED`, exit 0. The round-18 guard's message said it was "refusing
    to gate an unidentifiable tree"; the unidentifiable-tree case was precisely
    the one case it could not see.
    """
    repo = tmp_path / "repo"
    _repo(repo)
    script = Path(__file__).resolve().parent.parent / "scripts" / "gate_provenance.sh"
    result = subprocess.run(
        ["bash", str(script), "/nonexistent/python"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_GIT_ENV,
    )
    assert "# collect: unknown - COLLECTION FAILED" in result.stdout, result.stdout
    assert result.returncode != 0, result.stdout


def test_the_gate_refuses_a_tree_it_cannot_identify(tmp_path: Path) -> None:
    """The round-19 MEDIUM, end to end: release_gate.sh must actually refuse.

    The round-18 test only covered the script being ABSENT (exit 127). This
    covers the case the FATAL message names - a header that printed, but could
    not identify the tree.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parent.parent / "scripts"
    for name in ("release_gate.sh", "gate_provenance.sh"):
        (scripts / name).write_text(
            (source / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

    _repo(tmp_path)
    (tmp_path / "f.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "f.txt"], tmp_path)
    _git(["commit", "-qm", "init"], tmp_path)
    nested = tmp_path / "nested"
    _repo(nested)
    (nested / "n.txt").write_text("inner\n", encoding="utf-8")
    _git(["add", "n.txt"], nested)
    _git(["commit", "-qm", "inner"], nested)

    result = subprocess.run(
        ["bash", "scripts/release_gate.sh"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=_GIT_ENV,
    )
    assert result.returncode != 0, result.stdout
    assert "GATE PASSED" not in result.stdout, result.stdout
    assert "provenance header failed" in result.stdout, result.stdout
    assert "COULD NOT IDENTIFY THE TREE THAT RAN" in result.stdout, result.stdout
