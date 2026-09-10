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


def test_an_irrelevant_option_before_the_verb_still_finds_the_verb(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The `--allow-net explain` case from the round-11 parametrisation.

    Round 30b made a flag a verb does not read a refusal instead of a silent
    no-op, so `pq --allow-net explain X` now exits 2. The case is NOT dropped:
    the round-11 property was never about `--allow-net`'s meaning, it was that
    a leading option must not change which token is read as the verb - and the
    refusal naming `explain` is that property, proved by the new behaviour
    rather than around it. A wrong parse would say "Table.SelectRows does not
    use ..." or fail on a Path.
    """
    from pqtools.cli import main

    assert main(["--allow-net", "explain", "Table.SelectRows"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error MQUERY_ERROR: explain does not use"), err
    assert "--allow-net" in err, err


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
    builder, where the module documents a typed `ExportRefusal`.

    The property that must hold everywhere is the TYPE: an `ExportRefusal`,
    never a `TypeError` escaping the column builder. The MESSAGE depends on
    the environment, and this test first asserted only the with-extras one -
    so it passed here and failed on an install without pandas, where the
    dependency refusal comes first. That ordering is deliberate and is the
    more useful answer: with no pandas, fixing the input would not help.

    The first repair then claimed the regression was "just as reachable
    without the extras", which is false: `to_pandas` calls `_require_pandas()`
    BEFORE `_column_order()`, so with pandas absent every input below stops at
    the dependency refusal and the column builder is never entered. The floor
    arm was asserting a guard that cannot run where the defect lives. So the
    shape refusal is exercised through `_column_order` directly - it needs no
    extra and is the thing that used to raise `TypeError` - and the public
    entry points are checked separately for whichever refusal applies first.
    """
    import importlib

    from pqtools.export import ExportRefusal, _column_order, to_arrow, to_pandas

    # The regression itself, on a path that runs in every environment.
    for value in (5, "text", {"a": 1}, [1, 2]):
        with pytest.raises(ExportRefusal, match="table"):
            _column_order(value)

    # The public entry points answer whichever refusal applies first.
    # "installed" is decided by `import`, because `_require_pandas` decides it
    # that way: `find_spec` reports a package that is present but unimportable
    # (a broken binary ABI is the everyday case) as available, and the product
    # would still refuse it.
    for export, module in ((to_pandas, "pandas"), (to_arrow, "pyarrow")):
        try:
            importlib.import_module(module)
            expected = "table"
        except ImportError:
            expected = rf"{module} is not installed"
        for value in (5, "text", {"a": 1}, [1, 2]):
            with pytest.raises(ExportRefusal, match=expected):
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


def _git_env() -> dict[str, str]:
    """The environment the fixture repos run under.

    A FUNCTION, not a module-level literal, because the round-20 LOW was that
    the control asserting this behaviour re-typed the construction instead of
    calling it - so editing the real one would have left the control green
    while the fixtures leaked again. Same failure mode as the round-17 string
    grep, one level up.
    """
    return {
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
        env=_git_env(),
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
        env=_git_env(),
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
        env=_git_env(),
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

    # Call the real construction rather than re-typing it: a copy here would
    # stay green while the thing the fixtures actually use started leaking.
    env = _git_env()
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


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the permission bits, so add -A succeeds and writes no warning",
)
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
            env=_git_env(),
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
        env=_git_env(),
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
        env=_git_env(),
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
        env=_git_env(),
    )
    assert result.returncode != 0, result.stdout
    assert "GATE PASSED" not in result.stdout, result.stdout
    assert "provenance header failed" in result.stdout, result.stdout
    assert "COULD NOT IDENTIFY THE TREE THAT RAN" in result.stdout, result.stdout


def test_a_declared_submodule_is_not_mistaken_for_a_stray_repository(
    tmp_path: Path,
) -> None:
    """The round-20 MEDIUM: the round-19 gitlink net refused every submodule.

    `git read-tree HEAD` loads a declared submodule's `160000` entry into the
    scratch index and `git add -A` emits no warning for it, so any dirty gate
    run in a repo that uses submodules would have printed `COULD NOT IDENTIFY
    THE TREE THAT RAN` and exited 3. The stated defect was an UNDECLARED
    nested repository, not the presence of a gitlink.

    (The review's alternative - reject entries failing `git cat-file -e` -
    would not have worked either: a legitimate submodule's commit is not in the
    superproject's object store, verified, so that test rejects both alike.)

    Read this as a REINTRODUCTION guard, not as a control over shipped code:
    the net it guards against was deleted in the same commit, so against the
    current script this test passes for any implementation that does not
    inspect gitlinks. It earns the word "control" only against the mutation -
    reinstating the round-19 net makes it red - and the closeout counts it that
    way and no other. The distinction matters because "green" here means "the
    defect has not come back", not "this code is checked".
    """
    upstream = tmp_path / "upstream"
    _repo(upstream)
    (upstream / "s.txt").write_text("sub\n", encoding="utf-8")
    _git(["add", "s.txt"], upstream)
    _git(["commit", "-qm", "sub"], upstream)

    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)
    _git(["commit", "-qm", "init"], repo)
    added = subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(upstream),
            "sub",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if added.returncode != 0:
        pytest.skip(f"this git refuses local submodules: {added.stderr.strip()}")
    _git(["commit", "-qm", "add submodule"], repo)
    assert "160000" in _git(["ls-tree", "-r", "HEAD"], repo), "precondition"

    (repo / "marker.txt").write_text("dirty\n", encoding="utf-8")
    header = _provenance(repo)
    assert "COULD NOT IDENTIFY" not in header, header
    identity = _content_id(header)
    assert identity, header
    # And the submodule is present in it, recorded the way HEAD records it.
    listing = _git(["ls-tree", "-r", identity], repo)
    assert "160000" in listing, listing


def test_the_identity_holds_for_a_tree_large_enough_to_sigpipe(
    tmp_path: Path,
) -> None:
    """The round-20 HIGH was a SIGPIPE bug that only appears at real size.

    `grep -q` exits on the first match while `git ls-tree` is still writing;
    under `set -o pipefail` the 141 propagates, so a MATCH reported the same
    status as no-match. It returns 0 in a 3-entry fixture - the regime the
    round-19 control ran in - and 141 on this repo's own 233-entry tree.

    Every fixture above is tiny, which is exactly why none of them could see
    it. This one builds a tree big enough for the writer to block, so any
    future `| grep -q`-shaped check in this script is exercised in the regime
    where it breaks rather than the one where it works.

    600 is not a round number picked for comfort - the threshold was measured
    with the real writer, because it is not a clean byte count (an `awk`
    producing 40 KB returns 0 where `git ls-tree` producing 38 KB returns 141;
    it depends on how the writer flushes, so only `git ls-tree` answers it).
    Five runs at each size, on this machine:

        250 entries  16,642 bytes  ->  0 0 0 0 0
        300 entries  19,992 bytes  ->  141 141 141 141 141
        600 entries  40,092 bytes  ->  141 141 141 141 141

    So the boundary is between 250 and 300 entries and 600 is about twice it,
    which is the margin for a git whose output is laid out differently. The
    fixture costs 1.85s.
    """
    repo = tmp_path / "repo"
    _repo(repo)
    bulk = repo / "bulk"
    bulk.mkdir()
    for index in range(600):
        (bulk / f"f{index:04d}.txt").write_text(f"{index}\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "bulk"], repo)
    (repo / "marker.txt").write_text("dirty\n", encoding="utf-8")

    header = _provenance(repo)
    identity = _content_id(header)
    assert identity, header
    listing = _git(["ls-tree", "-r", identity], repo)
    assert len(listing.splitlines()) > 600, len(listing.splitlines())
    assert "marker.txt" in listing


# ---------------------------------------------------------------------------
# Diagnostics a person can act on
# ---------------------------------------------------------------------------
#
# `sales.pq:1:1: info M006: source function: File.Contents` is precise and
# tells a reader nothing about what to do. These pin the plain-English layer
# without loosening the machine-readable one CI depends on.

_ALL_SIX = (
    "let A = 1, A = 2, Dead = 3, "
    'Source = Web.Contents(Url), Password = "secret" in Missing'
)


def test_every_diagnostic_code_that_can_be_emitted_has_an_explanation() -> None:
    """The drift guard.

    A seventh code added later with no entry would print a bare jargon line
    and nothing would notice. This derives the codes from `check()` itself
    rather than from a hand-kept list, so the two cannot separate.
    """
    from pqtools.core import DIAGNOSTIC_HELP, FAILURE_HELP, check

    # BOTH branches of check(): the rule loop, and the ParseError branch that
    # only fires on source the parser rejects. The first version of this guard
    # fed it valid M only, so it could never reach the second branch - and
    # M_PARSE_ERROR, the code a person who cannot read M hits most often, was
    # missing from the help table with the guard green. A guard run only in
    # the regime where the defect cannot appear is not a guard.
    emitted = {item.code for item in check(_ALL_SIX, "query.pq")}
    assert emitted == {"M001", "M002", "M003", "M004", "M005", "M006"}, emitted
    unparseable = {item.code for item in check("let A = = 1 in A", "bad.pq")}
    assert unparseable == {"M_PARSE_ERROR"}, unparseable
    emitted |= unparseable

    missing = sorted(code for code in emitted if code not in DIAGNOSTIC_HELP)
    assert not missing, f"emitted with no plain-English entry: {missing}"

    # `pq explain` prints a severity but never sees a Diagnostic object, so
    # what it prints has to be checked against what `check()` really emits.
    # Round 26: this severity used to live in a SECOND dict keyed by the same
    # codes and was read with `.get(code, "")`, so the only thing standing
    # between a missing key and a silently blank severity was the set
    # equality this assertion used to make. It is a field on the entry now,
    # and - round 27 - a field with NO DEFAULT, so omitting it is a TypeError
    # at import rather than a blank severity a test has to notice. What is
    # left to check is the one thing the type cannot enforce: that the stated
    # value is TRUE.
    everything = list(check(_ALL_SIX, "query.pq")) + list(
        check("let A = = 1 in A", "bad.pq")
    )
    for code in emitted:
        actual = {item.severity for item in everything if item.code == code}
        assert DIAGNOSTIC_HELP[code].severity in actual, (code, actual)

    # A lint code has a severity; a failure has none. Both halves matter: an
    # empty severity makes `pq explain` fall back to printing the KIND, so a
    # lint code that lost its severity would print "M003 (lint diagnostic)"
    # and look deliberate.
    for code, entry in DIAGNOSTIC_HELP.items():
        assert entry.severity, f"{code} has no severity"
    for code, entry in FAILURE_HELP.items():
        if code not in DIAGNOSTIC_HELP:
            assert not entry.severity, (
                f"failure {code} claims severity {entry.severity}"
            )

    for code, entry in DIAGNOSTIC_HELP.items():
        assert entry.means.endswith("."), code
        assert entry.fix.endswith("."), code
        # Not `== .lower()`: "this is not valid Power Query" contains a proper
        # noun. The style rule is that a title does not START capitalised, so
        # it reads as a phrase after the code, not as a sentence.
        assert entry.title[:1].islower(), code


def test_the_explanation_is_printed_once_per_code_not_once_per_finding() -> None:
    """Repeating it is not thoroughness.

    A file with five unused steps printed the same sentence five times, and
    output that repeats itself is output people learn to skip - which costs
    more than the jargon it replaced.
    """
    from pqtools.core import Diagnostic, diagnostic_help, render_diagnostics

    four_of_one_code = [
        Diagnostic("q.pq", n, 5, "M004", "warning", f"unreachable let binding: b{n}")
        for n in range(1, 5)
    ]
    lines = render_diagnostics(four_of_one_code)
    help_entry = diagnostic_help("M004")
    assert help_entry is not None
    # Normalise whitespace: the sentence is wrapped across lines, so counting
    # exact-match lines would count the wrapping, not the repetition.
    flat = " ".join(" ".join(lines).split())
    assert flat.count(" ".join(help_entry.means.split())) == 1, lines
    # ...and every finding still gets its own machine line.
    assert len([line for line in lines if line.startswith("q.pq:")]) == 4, lines


def test_the_machine_readable_line_is_untouched_by_the_explanation() -> None:
    """`pq check | grep error` has to keep meaning what it looks like.

    The explanation is indented, so a line-counting or grep-anchored CI step
    cannot mistake it for a finding.
    """
    from pqtools.core import Diagnostic, render_diagnostics

    item = Diagnostic("q.pq", 2, 5, "M001", "error", "duplicate let binding: Source")
    lines = render_diagnostics([item])
    assert lines[0] == "q.pq:2:5: error M001: duplicate let binding: Source"
    assert lines[1].startswith("    ") and not lines[1].startswith("    q.pq")
    assert len([line for line in lines if ": error " in line]) == 1


def test_pq_explain_answers_for_a_diagnostic_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A person typing `pq explain` is holding whatever `pq check` printed.

    Answering only for function names would send them to the README for the
    codes and to the CLI for the functions - one lookup too many.
    """
    from pqtools.cli import main

    assert main(["explain", "M003"]) == 0
    out = capsys.readouterr().out
    assert "M003 (warning)" in out, out
    assert "What it means:" in out and "What to do:" in out, out
    assert "password" in out.lower(), out

    # lower-case too - people retype what they saw, not always exactly
    assert main(["explain", "m003"]) == 0
    assert "M003 (warning)" in capsys.readouterr().out

    # and the function-name path still works
    assert main(["explain", "Table.FuzzyNestedJoin"]) == 0
    assert "approximate" in capsys.readouterr().out.lower()


def test_pq_explain_json_carries_the_code_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pqtools.cli import main

    assert main(["explain", "M006", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "M006"
    assert payload["severity"] == "info"
    assert payload["fix"].startswith("Nothing")


def test_the_explanation_is_not_repeated_across_files_in_a_batch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-24 MEDIUM: the dedupe set was per-file, so it reset.

    `pq check 'src/**/*.pq'` - the README's own example - printed every
    sentence once per matching file, which is the same "output people learn to
    skip" defect the change was written to prevent, at batch scale.
    """
    from pqtools.cli import main

    source = "let A = 1, A = 2 in A\n"
    for name in ("one.pq", "two.pq", "three.pq"):
        (tmp_path / name).write_text(source, encoding="utf-8")

    assert (
        main(
            [
                "check",
                str(tmp_path / "one.pq"),
                str(tmp_path / "two.pq"),
                str(tmp_path / "three.pq"),
            ]
        )
        == 2
    )
    out = capsys.readouterr().out
    flat = " ".join(out.split())
    sentence = "Two steps in this query are called the same thing"
    assert flat.count(sentence) == 1, out
    # ...and every file still reports every one of its own findings. Two per
    # file, not one: `let A = 1, A = 2` names the duplicate at both positions.
    assert out.count("error M001:") == 6, out
    for name in ("one.pq", "two.pq", "three.pq"):
        assert name in out, (name, out)


def test_an_unparseable_file_gets_the_plain_english_line_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-24 HIGH: M_PARSE_ERROR had no entry, so the single most
    common finding rendered as a bare jargon line.

    `check()` emits it from the ParseError branch rather than the rule loop,
    which is why it was missed - and why the guard, fed valid M, could not see
    that it was missed.
    """
    from pqtools.cli import main

    bad = tmp_path / "bad.pq"
    bad.write_text("let A = = 1 in A\n", encoding="utf-8")
    assert main(["check", str(bad)]) == 2
    out = capsys.readouterr().out
    assert "M_PARSE_ERROR" in out, out
    assert "could not read this file" in out, out


def test_pq_explain_answers_for_the_parse_error_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """It previously fell through to the function-name branch and answered
    "M_PARSE_ERROR is not a name pqtools recognizes as a documented Power
    Query M function", which is an actively wrong answer - and llms.txt says
    `pq explain` takes any diagnostic code.
    """
    from pqtools.cli import main

    assert main(["explain", "M_PARSE_ERROR"]) == 0
    out = capsys.readouterr().out
    assert "M_PARSE_ERROR (error)" in out, out
    assert "not a name pqtools recognizes" not in out, out
    assert "What to do:" in out, out


def _every_code_the_cli_can_show(tmp_path: Path) -> set[str]:
    """Every code that can reach a user, derived rather than listed.

    Three sources, none of them a hand-kept list:
      - `check()`, both branches (rule loop and ParseError)
      - every `MQueryError` subclass's `.code`, walked at runtime
      - the code `_run_check_batch` substitutes for a bare `OSError`, obtained
        by actually provoking one - it is a literal in cli.py, not a class
        attribute, so nothing but running it will reveal it
    """
    import importlib
    import json as _json
    import pkgutil

    import pqtools
    from pqtools.cli import main
    from pqtools.core import MQueryError, check

    # Round 26: this was `except Exception: pass`, which is the one thing a
    # derivation must not do - a module that stops importing takes its error
    # classes out of the derived set, and every guard built on that set stays
    # green while a real code loses its plain-English entry.
    #
    # The swallow was there for "optional extras may be absent". Measured:
    # every third-party import in this package (openpyxl, pandas, pyarrow) is
    # already INSIDE a function, so no pqtools module can fail to import
    # because an extra is missing. The clause was protecting against a
    # hazard that cannot happen while hiding every hazard that can - a
    # syntax error, a circular import, a module that raises at import time.
    # So there is no `except`: if a module will not import, that is the
    # finding, and ModuleNotFoundError already names the package.
    imported = 0
    for module in pkgutil.walk_packages(pqtools.__path__, "pqtools."):
        importlib.import_module(module.name)
        imported += 1
    # The instrument itself: a walk that imports nothing would make every
    # assertion below pass by finding nothing to check.
    assert imported >= 20, f"the module walk only imported {imported} modules"

    codes = {item.code for item in check(_ALL_SIX, "query.pq")}
    codes |= {item.code for item in check("let A = = 1 in A", "bad.pq")}

    def walk(cls: type) -> None:
        for sub in cls.__subclasses__():
            code = getattr(sub, "code", None)
            if isinstance(code, str):
                codes.add(code)
            walk(sub)

    walk(MQueryError)
    base = getattr(MQueryError, "code", None)
    if isinstance(base, str):
        codes.add(base)

    missing_a = tmp_path / "does-not-exist-a.pq"
    missing_b = tmp_path / "does-not-exist-b.pq"
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.suppress(SystemExit):
        main(["check", str(missing_a), str(missing_b), "--json"])
    payload = _json.loads(buffer.getvalue())
    codes |= {row["code"] for row in payload}
    return codes


def test_every_code_the_cli_can_show_a_user_has_a_plain_english_entry(
    tmp_path: Path,
) -> None:
    """The round-25 MEDIUM: the guard still ran where the defect could not appear.

    Round 24 widened it from one branch of `check()` to two, which closed the
    `M_PARSE_ERROR` hole - and left the whole FAILURE-code family outside it.
    `pq explain M_IO_ERROR` answered "is not a name pqtools recognizes as a
    documented Power Query M function", an actively wrong answer, for twelve
    codes. `M_IO_ERROR` in particular is emitted by `_run_check_batch` itself
    and never passes through `check()` at all.
    """
    from pqtools.core import DIAGNOSTIC_HELP, FAILURE_HELP

    codes = _every_code_the_cli_can_show(tmp_path)
    assert "M_IO_ERROR" in codes, "the OSError path stopped emitting its code"
    assert "M_PARSE_ERROR" in codes and "M001" in codes, codes

    explained = set(DIAGNOSTIC_HELP) | set(FAILURE_HELP)
    missing = sorted(code for code in codes if code not in explained)
    assert not missing, f"reachable codes with no plain-English entry: {missing}"

    # Round 26: the assertion above only ran code -> entry, so an INVENTED
    # code - one added to FAILURE_HELP and to llms.txt's table and raised by
    # nothing - satisfied it, satisfied the llms.txt set equality (both sides
    # were edited), and satisfied the "is this entry what a user is shown"
    # check (`pq explain` will happily print any entry it holds). Three nets,
    # all forward-facing, none of which asks whether the code exists.
    #
    # These are the same set, so say so. If a new code is genuinely reachable
    # by a path this derivation cannot see - `M_IO_ERROR` was, until the
    # OSError above was provoked to reveal it - extend the derivation to
    # provoke it. Deleting this assertion to get green would restore exactly
    # the hole it closes.
    invented = sorted(code for code in explained if code not in codes)
    assert not invented, (
        f"explained codes nothing can report: {invented}. Either the code is "
        "unreachable and its entry is fiction, or the derivation above cannot "
        "see the path that reports it - find out which."
    )


def test_pq_explain_never_calls_a_real_code_an_unrecognized_function(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round-25 HIGH, stated as the property that was actually violated.

    A wrong answer is worse than a gap, and llms.txt promises `pq explain`
    takes "either a diagnostic code or an M function name".
    """
    from pqtools.cli import main

    for code in sorted(_every_code_the_cli_can_show(tmp_path)):
        capsys.readouterr()
        assert main(["explain", code]) == 0, code
        out = capsys.readouterr().out
        assert "is not a name pqtools recognizes" not in out, (code, out)
        assert "What to do:" in out, (code, out)


def test_the_failure_code_table_matches_the_documented_one() -> None:
    """FAILURE_HELP and llms.txt's failure table cannot grow apart.

    The prose differs on purpose - llms.txt is written for an agent parsing
    error output, these are written for a person - but the CODE SET has to
    agree, or one of them is lying about what pqtools can report.
    """
    from pqtools.core import FAILURE_HELP

    text = (Path(__file__).resolve().parent.parent / "llms.txt").read_text(
        encoding="utf-8"
    )
    start = text.index("| Code | Meaning | What to do |")
    end = text.index("\n\n", start)
    # `[A-Z0-9_]+`, not `[A-Z_]+`: the lint codes are M001..M006, and the
    # same parser is used on their table below. With the digit class missing
    # every one of them read as "not a row", so the lint table could have
    # been empty and the assertion would still have had rows to compare.
    documented = set(re.findall(r"^\| `([A-Z0-9_]+)`", text[start:end], re.M))
    assert documented, "llms.txt failure table stopped parsing"
    assert documented == set(FAILURE_HELP), {
        "only in llms.txt": sorted(documented - set(FAILURE_HELP)),
        "only in FAILURE_HELP": sorted(set(FAILURE_HELP) - documented),
    }


def test_a_code_in_both_tables_has_one_entry() -> None:
    """The round-26 MEDIUM: `M_PARSE_ERROR` had two entries and one was dead.

    `_run_explain` consults `diagnostic_help()` first, so a code present in
    both tables can only ever show the LINT entry. The failure entry was
    therefore unreachable from the day it was written - and had already
    drifted, carrying a different title and a different fix. Nothing noticed,
    because the union assertion above only asks whether a code has AN entry.
    """
    import contextlib
    import io
    import json

    from pqtools.cli import main
    from pqtools.core import DIAGNOSTIC_HELP, FAILURE_HELP

    for code in set(DIAGNOSTIC_HELP) & set(FAILURE_HELP):
        assert DIAGNOSTIC_HELP[code] is FAILURE_HELP[code], (
            f"{code} has two entries; only the lint one can ever be shown"
        )

    # Round 27 LOW: llms.txt states "severity is the lint severity ... or ""
    # for a failure code", and the one code in both tables contradicts it -
    # it sits in the FAILURE table but answers as a lint diagnostic, because
    # the lint table is consulted first. An agent branching on which table it
    # found the code in gets the opposite of what it is told. The document
    # now names the exception; this holds the document to it.
    # Both loops here iterate the intersection, so both go quietly vacuous if
    # it ever empties - drop `M_PARSE_ERROR` from `FAILURE_HELP` and these
    # assertions stop running rather than fail. Name the expected member.
    shared = set(DIAGNOSTIC_HELP) & set(FAILURE_HELP)
    assert shared == {"M_PARSE_ERROR"}, shared
    llms = (Path(__file__).resolve().parent.parent / "llms.txt").read_text(
        encoding="utf-8"
    )
    for code in sorted(shared):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            assert main(["explain", code, "--json"]) == 0, code
        payload = json.loads(buffer.getvalue())
        assert payload["kind"] == "lint diagnostic", (code, payload)
        assert payload["severity"] == DIAGNOSTIC_HELP[code].severity, (code, payload)
        assert f"`{code}`, which appears in BOTH tables" in llms, (
            f"{code} answers as a lint diagnostic despite being documented as "
            "a failure code, and llms.txt does not name the exception"
        )


def test_every_explained_code_is_the_answer_a_user_actually_gets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The round-26 LOW: nothing proved the REVERSE of the coverage guard.

    Every existing check runs code -> entry: does this reachable code have an
    explanation? An entry that no code reaches, or that a second entry
    shadows, satisfies all of them. Run the other way, against the CLI rather
    than the tables, this finds both: an invented code prints nothing a user
    can reach, and a shadowed one prints somebody else's words.
    """
    from pqtools.cli import main
    from pqtools.core import DIAGNOSTIC_HELP, FAILURE_HELP

    for label, table in (
        ("DIAGNOSTIC_HELP", DIAGNOSTIC_HELP),
        ("FAILURE_HELP", FAILURE_HELP),
    ):
        for code, entry in table.items():
            capsys.readouterr()
            assert main(["explain", code]) == 0, code
            out = capsys.readouterr().out
            assert entry.title in out, (
                f"{label}[{code}] is dead: `pq explain {code}` never prints "
                f"its title {entry.title!r}. Printed: {out!r}"
            )
            assert entry.means in " ".join(out.split()), (label, code, out)


def test_a_code_shaped_name_is_never_answered_as_a_function_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The round-26 MEDIUM and the round-27 HIGH it caused.

    llms.txt's "It never answers a real code as though it were an unrecognised
    function name" held only for the codes this version happens to report,
    because the guard derived exactly those. A code from a newer pqtools still
    got "may be a typo, an internal or undocumented name, or something outside
    the M standard library (a query name, a variable, a record field)" - four
    suggestions, none ever true of a code.

    The round-26 fix then matched that shape against `name.upper()`, which
    inverted it: `my_step` upper-cased to `MY_STEP` and a perfectly ordinary
    step name was answered "is not a code this version of pqtools reports".
    Round 26's own guard could not have caught it - it asked whether the shape
    swallows a DOCUMENTED name, and this branch is reached only by names that
    are NOT documented. The guard ran where the defect could not appear, for
    the seventh time in this audit. So the boundary is pinned here on both
    sides, with the identifiers a person actually types.
    """
    from pqtools.cli import _CODE_SHAPED, main

    # Code-shaped: the CODE reading leads, and the code list is printed so the
    # reader is not sent to a document. `M007` matters most - `pq check`
    # prints M001..M006, so the next lint code is the likeliest thing a user
    # holds, and it has no underscore.
    #
    # This used to assert `"not a name pqtools recognizes" not in out`, which
    # is no longer the property: round 28 made BOTH branches say both things,
    # because the shape cannot be made exact and no answer may depend on it
    # being exact. What is asserted is which reading LEADS.
    for name in ("M_FUTURE_ERROR", "NODE_ERROR2", "MQUERY_ERROR2", "M007", "M999"):
        capsys.readouterr()
        assert main(["explain", name]) == 0, name
        out = " ".join(capsys.readouterr().out.split())
        assert out.startswith(
            f"{name} is not a code this version of pqtools reports"
        ), (name, out)
        assert "M_IO_ERROR" in out and "M001" in out, (name, out)

    # NOT code-shaped: every one of these is a legal M identifier, so the NAME
    # reading leads.
    #
    # Round 27 listed only lower/mixed-case names here, so it ran in a regime
    # where its own residual defect could not appear - the EIGHTH instance of
    # the pattern, written by the round that named the pattern. The ALL-CAPS
    # entries are the ones that matter: `TOTAL_SALES`, `CHANGED_TYPE`, `A_1`
    # are ordinary M identifiers, and round 27's shape ("ALL-CAPS with an
    # underscore") answered every one of them "is not a code this version of
    # pqtools reports". The list is built from what a person names a step,
    # not from what happens to fall outside today's regex.
    #
    # Round 29: `M_` was pinned here but `NODE_` and `MQUERY_` were not, and
    # their alternatives lacked the trailing-alnum anchor `M_` has - so a bare
    # `NODE_` led with the code reading while a bare `M_` did not, and the
    # asymmetry sat outside the regime this guard measures. Ninth instance.
    # Every family the shape knows about is pinned here now - bare AND
    # double-underscore for all three, since round 29 pinned `NODE__` but not
    # `M__`, leaving the family the anchor was originally written for as the
    # only one whose double-underscore case sat outside the guard.
    for name in (
        "TOTAL_SALES",
        "CHANGED_TYPE",
        "A_1",
        "REMOVED_COLUMNS",
        "my_step",
        "source_data",
        "raw_data_2",
        "Result_2",
        "M_",
        "M__",
        "NODE_",
        "NODE__",
        "MQUERY_",
        "MQUERY__",
        "m007",
    ):
        assert not _CODE_SHAPED.match(name), name
        capsys.readouterr()
        assert main(["explain", name]) == 0, name
        out = " ".join(capsys.readouterr().out.split())
        assert out.startswith(f"{name} is not a name pqtools recognizes"), (name, out)

    # The property that survives the shape being WRONG, which it will be:
    # `M_TOTAL` is a legal step name that matches the prefix families, and
    # some future code family will not match them. So both readings are
    # stated either way, and nothing is ever told flatly that it is the other
    # thing. A dotted name is the one unambiguous case (no code contains a
    # dot) and keeps the plain function-name wording.
    for name in (
        "M_TOTAL",
        "TOTAL_SALES",
        "M_FUTURE_ERROR",
        "M007",
        "my_step",
        "NODE_COUNT",
    ):
        capsys.readouterr()
        assert main(["explain", name]) == 0, name
        out = " ".join(capsys.readouterr().out.split())
        assert "not a code" in out or "not one of the codes" in out, (name, out)
        assert "not a name pqtools recognizes" in out, (name, out)

    capsys.readouterr()
    assert main(["explain", "Table.Zzz"]) == 0
    dotted = " ".join(capsys.readouterr().out.split())
    assert "not a name pqtools recognizes" in dotted, dotted
    assert "codes pqtools reports" not in dotted, dotted

    for spelling in ("M_IO_ERROR", "m_io_error", "M001", "m001"):
        capsys.readouterr()
        assert main(["explain", spelling]) == 0, spelling
        assert "is not a code" not in capsys.readouterr().out, spelling

    # And the shape swallows no name pqtools already knows.
    #
    # This used to hold up a SENTENCE: the code branch ran BEFORE the
    # `BUILTINS` and `catalog.explain` lookups yet printed "not a name pqtools
    # recognizes as a documented Power Query M function", a claim nothing in
    # that branch had checked, and this assertion was the only thing making it
    # true. Round 29 moved the branch below both lookups, so the sentence
    # verifies itself.
    #
    # It did not stop holding something up - it changed WHAT. It is now the
    # only thing keeping llms.txt's stated heuristic ("a code PREFIX family
    # leads with the code reading") from acquiring a silent second exception:
    # a code-shaped name that is also a builtin or documented would lead with
    # the catalog reading instead, and nothing else would say so. Still worth
    # having, still load-bearing, for a different claim.
    # BUILTINS as well as DOCUMENTED - 93 builtins are in neither.
    from pqtools.catalog import DOCUMENTED
    from pqtools.evaluate import BUILTINS

    caught = sorted(n for n in set(DOCUMENTED) | set(BUILTINS) if _CODE_SHAPED.match(n))
    assert not caught, f"code shape swallows real M names: {caught}"


def test_the_lint_code_table_matches_the_documented_one() -> None:
    """The other half of the failure-table check, which only had one half.

    llms.txt lists the lint codes WITH their severities, and nothing compared
    that table to the source of truth. It is also the table whose codes carry
    digits, so it is the one the `[A-Z_]+` row pattern silently read as
    empty - a parser that finds no rows cannot disagree with anything.
    """
    from pqtools.core import DIAGNOSTIC_HELP

    text = (Path(__file__).resolve().parent.parent / "llms.txt").read_text(
        encoding="utf-8"
    )
    start = text.index("| Code | Severity | Means |")
    end = text.index("\n\n", start)
    rows = re.findall(r"^\| `([A-Z0-9_]+)` \| (\w+) \|", text[start:end], re.M)
    assert len(rows) == len(DIAGNOSTIC_HELP), (rows, sorted(DIAGNOSTIC_HELP))
    documented = dict(rows)
    assert documented.keys() == DIAGNOSTIC_HELP.keys(), {
        "only in llms.txt": sorted(documented.keys() - DIAGNOSTIC_HELP.keys()),
        "only in DIAGNOSTIC_HELP": sorted(DIAGNOSTIC_HELP.keys() - documented.keys()),
    }
    for code, severity in documented.items():
        assert severity == DIAGNOSTIC_HELP[code].severity, (
            code,
            severity,
            DIAGNOSTIC_HELP[code].severity,
        )


# The sources the rename guard's decision is held against. Deliberately mixed:
# legal M, illegal M, blockers inside strings and comments (where they are
# still blockers, because the guard is textual on purpose), and the shapes a
# real Power BI query has.
_RENAME_CORPUS = (
    "",
    "let a = 1 in a",
    "let Source = 1, Result = Source + 1 in Result",
    'let #"My Step" = 1 in #"My Step"',
    "let a = [x = 1] in a[x]",
    'let a = Table.SelectRows(t, each [Region] <> "") in a',
    "let f = (x) => x + 1 in f(1)",
    'let a = "a [bracket] in a string" in a',
    "let a = 1 in a // a [bracket] in a comment\n",
    'let a = "café" in a',
    "let a = 1 in a // café\n",
    "let\n    Source = 1,\n    Next = Source\nin\n    Next\n",
    'let Source = Csv.Document(File.Contents("d.csv")) in Source',
)


def test_the_rename_guard_refuses_exactly_what_it_always_did() -> None:
    """The refusal message got better. The net did not get smaller.

    The standing constraint on this guard is explicit: it may not be loosened
    without binding-aware analysis of the parse tree, because a rename that is
    right most of the time silently alters a query. So the DECISION is held
    here against the original one-line expression, source by source - the
    improvement is only allowed to change what the refusal SAYS.
    """
    from pqtools.core import _rename_blocker

    def original(source: str) -> bool:
        return '#"' in source or "[" in source or "=>" in source or not source.isascii()

    for source in _RENAME_CORPUS:
        assert (_rename_blocker(source) is not None) == original(source), source

    # And the corpus is not vacuous in either direction - a corpus of all
    # blockers, or of none, would satisfy the equality above while proving
    # nothing about the half it does not contain.
    decisions = {original(source) for source in _RENAME_CORPUS}
    assert decisions == {True, False}, decisions


def test_a_refused_rename_names_the_construct_and_where_it_is() -> None:
    """The round-24..29 plain-English work reached `check` but not this verb.

    "quoted, record, lambda, or non-ASCII rename is unsupported" is four things
    to hunt for by hand across a whole file, and a real Power BI query is
    hundreds of lines. The guard already knows which one it found; it simply
    was not saying.
    """
    from pqtools.core import RenameRefusal, _rename_blocker, rename

    cases = (
        ('let #"A B" = 1 in #"A B"', 'a quoted identifier (#"...")', 1, 5),
        ("let a = [x = 1] in a", "a record literal or field access ([...])", 1, 9),
        ("let f = (x) => x in f", "a lambda (=>)", 1, 13),
        ('let a = "café" in a', "a non-ASCII character", 1, 13),
    )
    for source, what, line, column in cases:
        blocker = _rename_blocker(source)
        assert blocker is not None, source
        assert blocker[0].startswith(what), (source, blocker)
        assert (blocker[1], blocker[2]) == (line, column), (source, blocker)

    # Multi-line: the position has to be the line and column a person can
    # navigate to, not a byte offset.
    multi = "let\n    Source = 1,\n    Next = [x = 1]\nin\n    Next\n"
    blocker = _rename_blocker(multi)
    assert blocker is not None
    assert (blocker[1], blocker[2]) == (3, 12), blocker
    assert multi.splitlines()[2][11] == "[", multi.splitlines()[2]

    # The earliest blocker wins when several are present, so the position
    # always points at something real rather than at whichever check ran first.
    both = "let f = (x) => x, a = [y = 1] in a"
    blocker = _rename_blocker(both)
    assert blocker is not None and blocker[0] == "a lambda (=>)", blocker

    # And the message a user actually sees carries all of it.
    try:
        rename("let a = [x = 1] in a", "a", "b")
    except RenameRefusal as error:
        text = str(error)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("the guard did not refuse")
    assert "record literal or field access" in text, text
    assert "line 1 column 9" in text, text
    assert "pq explain M_RENAME_REFUSED" in text, text


def test_every_option_is_classified_for_every_verb() -> None:
    """The table cannot silently fall behind the parser.

    A hand-kept map from option to verbs is exactly the shape that drifts -
    `DIAGNOSTIC_SEVERITY` was a second dict keyed by the same codes and it
    drifted within two rounds. So the table is checked against the parser
    itself: every option the parser defines must be classified, every
    classified name must be a real option, and every verb named must be a real
    verb. A new flag added without a decision fails here rather than being
    accepted everywhere by default.
    """
    from pqtools.cli import _OPTION_VERBS, _build_parser

    # Round 32: this read the parser through a regex over cli.py's TEXT
    # (`parser.add_argument(\s*"--([a-z-]+)"`), so an option declared with a
    # short alias first, or containing a digit or a capital, never entered
    # `defined` - the set equality below still passed and the new flag was
    # universal by default, which is exactly the drift this test exists for.
    # The verb list was a hardcoded alternation, so a verb REMOVED from the
    # parser could not be detected either. Both come off the parser object now.
    parser = _build_parser()
    defined = {
        action.dest
        for action in parser._actions  # noqa: SLF001 - argparse has no public API
        if action.option_strings and action.dest != "help"
    }
    verbs = {
        choice
        for action in parser._actions  # noqa: SLF001
        if action.dest == "command" and action.choices
        for choice in action.choices
    }
    assert verbs, "the parser stopped declaring its verbs"

    assert defined == set(_OPTION_VERBS), {
        "parser has, table lacks": sorted(defined - set(_OPTION_VERBS)),
        "table has, parser lacks": sorted(set(_OPTION_VERBS) - defined),
    }
    for name, allowed in _OPTION_VERBS.items():
        assert allowed, f"{name} is allowed on no verb at all"
        unknown = allowed - verbs
        assert not unknown, (name, sorted(unknown))
    # Not every option may be universal, or the check would be a no-op that
    # still passed every assertion above.
    assert any(len(v) < len(verbs) for v in _OPTION_VERBS.values())


def test_a_verb_refuses_a_flag_it_does_not_use_instead_of_ignoring_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Found by running all twelve verbs, not by the suite.

    `pq replace-source q.pq --name Source --source "..."` replaced the WHOLE
    FILE while the user had every reason to read `--name` as scoping the edit
    to one step, and `pq rename q.pq --old a --new b --member Nope --source xx`
    renamed and dropped two flags without a word. This package refuses a
    `Username` field in an M options record BY NAME rather than ignoring it;
    its own flags were held to a lower standard than the M it reads.
    """
    from pqtools.cli import main

    target = tmp_path / "q.pq"
    original = "let a = 1 in a\n"
    target.write_text(original, encoding="utf-8")

    refused = (
        (
            [
                "replace-source",
                str(target),
                "--name",
                "A",
                "--source",
                "let z = 1 in z",
            ],
            "--name",
        ),
        (
            ["rename", str(target), "--old", "a", "--new", "b", "--member", "X"],
            "--member",
        ),
        (["check", str(target), "--to", "parquet"], "--to"),
        (["check", str(target), "--write"], "--write"),
        (["list", str(target), "--old", "X"], "--old"),
        # Round 32: every case above passes an option at a value that DIFFERS
        # from its default, so all of them passed while the check compared
        # values instead of presence - the guard ran in the one regime where
        # that defect cannot appear. `--format` defaults to "json", so these
        # three are the missing regime, in the spellings argparse accepts.
        (["check", str(target), "--format", "json"], "--format"),
        (["check", str(target), "--format=json"], "--format"),
        (["check", str(target), "--form", "json"], "--format"),
    )
    for argv, flag in refused:
        capsys.readouterr()
        assert main(argv) == 2, argv
        err = capsys.readouterr().err
        assert flag in err, (argv, err)
        assert "does not use" in err, (argv, err)
        # A refusal that already wrote is not a refusal.
        assert target.read_text(encoding="utf-8") == original, argv

    # And the check must not fire on correct input - a guard that reddens a
    # legitimate invocation is worse than the silence it replaced. The first
    # cut of it compared against a hardcoded default instead of the parser's,
    # and reported `--format` (which defaults to "json") on every verb.
    for argv in (
        ["check", str(target), "--json"],
        ["eval", str(target)],
        ["eval", str(target), "--format", "json"],
        ["explain", "Text.From", "--json"],
        ["format", str(target)],
        ["rename", str(target), "--old", "a", "--new", "b"],
        ["parse", str(target), "--json"],
        ["dependencies", str(target)],
    ):
        capsys.readouterr()
        assert main(argv) == 0, (argv, capsys.readouterr().err)


def _one_section_container(tmp_path: Path) -> Path:
    """A one-section .pbix built here, not read from `.samples/`.

    Round 32: this copied `.samples/real-powerbi-fuzzy-matching.pbix`, which
    `.gitignore` excludes and CLAUDE.md says is never committed - so both
    round-31c guards SKIPPED in every clean clone, and the preview/write
    disagreement they were written for was unguarded for anyone but me. A test
    that always skips is not a test.

    Round 33: that replacement then inlined the DataMashup layout a THIRD time
    (`test_containers.py` builds it, and line 687 of this file builds it
    again), on a stated worry that importing across test modules is not
    guaranteed by pytest's rootdir. The suite had already disproved that
    worry: `test_write_validation.py` and `test_container_workflow.py` both
    import these helpers from `test_containers`. One copy of the byte layout
    means one place to fix when the format understanding changes.
    """
    from test_containers import _blob, _pbix

    m_text = "section Section1;\n\nshared Existing = let x = 1 in x;\n"
    target = tmp_path / "container.pbix"
    target.write_bytes(_pbix(_blob(m_text)))
    return target


def test_pq_add_refuses_a_query_that_does_not_parse_before_it_previews(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 31b: `pq add` validated NOTHING until `--write`.

    `pq add c.pbix --name Bad --source "let x = = 1 in x"` printed the composed
    section document and exited 0 - a preview whose whole purpose is "show me
    what would happen" answering with something that cannot happen, while the
    write path refused the identical bytes. Two verbs disagreeing about the
    same input is the shape this package exists to avoid.
    """
    from pqtools.cli import main

    container = _one_section_container(tmp_path)
    before = container.read_bytes()

    for argv_tail in ([], ["--write"]):
        capsys.readouterr()
        code = main(
            ["add", str(container), "--name", "Bad", "--source", "let x = = 1 in x"]
            + argv_tail
        )
        assert code == 2, argv_tail
        captured = capsys.readouterr()
        assert "M_PARSE_ERROR" in captured.err, captured
        assert "--source does not parse" in captured.err, captured
        # The position must be in the source the USER wrote. The write path
        # used to report `65:22` for a one-line `--source` - a real position in
        # a document they never see.
        assert "1:9" in captured.err, captured
        assert "65:" not in captured.err, captured
        assert "shared Bad" not in captured.out, captured.out
        assert container.read_bytes() == before, argv_tail


def test_pq_add_separates_a_bad_query_from_one_that_breaks_the_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The composed-document check is reachable, and this is how.

    A query ending in a `//` comment parses perfectly well on its own. Appended
    as `shared Name = <body>;` the section's terminating `;` lands inside that
    comment, so the document does not parse - and the position is in the
    composed text, not in what the user typed. Both failures are refused; they
    say different things because they are different mistakes.
    """
    from pqtools.cli import main

    container = _one_section_container(tmp_path)
    before = container.read_bytes()
    body = "let x = 1 in x\n// note: quarterly only"

    capsys.readouterr()
    assert main(["add", str(container), "--name", "C", "--source", body]) == 2
    err = capsys.readouterr().err
    assert "parses alone but not inside this section" in err, err
    assert "not in your --source" in err, err
    assert "--source does not parse" not in err, err
    assert container.read_bytes() == before

    # The same body without the comment is fine, so the refusal is about the
    # comment and not about the query.
    capsys.readouterr()
    assert (
        main(["add", str(container), "--name", "C", "--source", "let x = 1 in x"]) == 0
    )
    out = capsys.readouterr().out
    assert "shared C = let x = 1 in x;" in out, out
    assert container.read_bytes() == before, "preview must not write"

    # And it writes, and the result is listable and evaluable.
    assert (
        main(
            [
                "add",
                str(container),
                "--name",
                "C",
                "--source",
                "let x = 1 in x",
                "--write",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["list", str(container)]) == 0
    assert "C" in capsys.readouterr().out
    capsys.readouterr()
    assert main(["eval", str(container), "--member", "C"]) == 0
    assert capsys.readouterr().out.strip() == "1"


def test_pq_add_refuses_a_source_that_smuggles_a_second_query(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 33's HIGH, and it was my own round-32 regression.

    Round 32 moved `parse(body)` off the success path on the reasoning that it
    "only ever chose a better MESSAGE". It was also the containment check.
    Without it `--source` accepts a whole `;`-separated run of declarations,
    and the composed document is still valid M - so it parsed, previewed
    clean, exited 0, and `--write` committed a section that REDEFINED a member
    `add` refuses by name to touch four lines earlier.

    The duplicate-name guard is not enough on its own: it reads `--name`,
    which here is an innocent new name. The smuggled member rides in the
    value.
    """
    from pqtools.cli import main

    container = _one_section_container(tmp_path)
    before = container.read_bytes()

    capsys.readouterr()
    code = main(
        [
            "add",
            str(container),
            "--name",
            "X",
            "--source",
            "1; shared Existing = 2",
        ]
    )
    captured = capsys.readouterr()
    assert code != 0, captured.out
    assert "Existing" in captured.err, captured.err
    assert "redefines" in captured.err, captured.err
    # The preview must not have printed the document it refused to compose.
    assert "shared X" not in captured.out, captured.out

    # A third query is the same defect wearing a different name: the keys
    # differ here, the sources differ above, and one check has to catch both.
    capsys.readouterr()
    assert (
        main(["add", str(container), "--name", "X", "--source", "1; shared Y = 2"]) != 0
    )
    assert "adds 'Y'" in capsys.readouterr().err

    # Round 34: a member that is not `shared` is in neither dict, so neither
    # "adds" nor "redefines" can name it. It was refused - correctly - with
    # "changes the section", which tells the reader nothing, from the verb
    # whose whole point is refusing BY NAME. The text that rode along is what
    # they need to see.
    capsys.readouterr()
    assert (
        main(["add", str(container), "--name", "X", "--source", "1; Hidden = 2"]) != 0
    )
    private = capsys.readouterr().err
    assert "second section member" in private, private
    assert "Hidden = 2" in private, private
    assert "changes the section" not in private, private

    # --write is the expensive half. Nothing may have reached the file, by
    # either route, and no backup should exist for a write that never began.
    assert container.read_bytes() == before
    capsys.readouterr()
    assert (
        main(
            [
                "add",
                str(container),
                "--name",
                "X",
                "--source",
                "1; shared Existing = 2",
                "--write",
            ]
        )
        != 0
    )
    capsys.readouterr()
    assert container.read_bytes() == before
    assert not list(tmp_path.glob("*.bak"))

    # Vacuity: the ordinary add this guard sits in front of still works, so
    # the refusal above is about the smuggled member and not about `add`.
    capsys.readouterr()
    assert main(["add", str(container), "--name", "X", "--source", "1"]) == 0
    assert "shared X = 1;" in capsys.readouterr().out


def test_pq_add_answers_in_json_when_asked_on_both_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 33's MEDIUM: `--json` on `add` had no test on either path.

    `test_every_option_is_classified_for_every_verb` passes whether or not
    `_run_add` ever READS `args.json` - it checks that the option is
    classified, not that it is consumed. That gap is exactly how `--json` came
    to be classified as meaningful for `add` and then honoured by nothing but
    the shared error path, dropping silently on success for a whole round.
    Classification and consumption are different questions, so this asks the
    second one.
    """
    from pqtools.cli import main

    container = _one_section_container(tmp_path)

    capsys.readouterr()
    assert main(["add", str(container), "--name", "X", "--source", "1", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["name"] == "X"
    assert preview["written"] is False
    assert "shared X = 1;" in preview["source"]

    capsys.readouterr()
    assert (
        main(
            ["add", str(container), "--name", "X", "--source", "1", "--json", "--write"]
        )
        == 0
    )
    written = json.loads(capsys.readouterr().out)
    assert written["name"] == "X"
    assert written["written"] is True
    assert Path(written["backup"]).exists()


def test_a_refusal_names_the_flag_the_user_typed_not_one_built_from_the_dest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 34: the third dest-inference site, and the one a user READS.

    The round that removed flag-text inference from `_options_present` and
    from the documented-command check left the refusal MESSAGE rendering the
    flag as `--{dest}` with underscores swapped for dashes. An option declared
    `dest="out"` on `--out-file` is detected correctly and then reported as
    `--out` - a flag that does not exist, in the sentence telling someone
    which flag to remove.

    No shipped option declares a divergent `dest` today, so a test that only
    walks the real parser would pass whether or not the fix is present: it
    would run in the one regime where the defect cannot appear. This supplies
    the divergence instead, which is the only way the guard can go red.
    """
    import argparse

    from pqtools import cli
    from pqtools.core import MQueryError

    probe = argparse.ArgumentParser(prog="pq")
    probe.add_argument("command")
    # Round 35: the short alias is declared FIRST on purpose. Rendering
    # `option_strings[0]` would say `-o` while `--help`, `llms.txt` and
    # `_OPTION_VERBS` all say `--out-file`, and `_build_parser`'s own docstring
    # names that declaration order as a case this parser must survive. Without
    # the alias here, this test passes for both the fix and the defect.
    probe.add_argument("-o", "--out-file", dest="out")
    monkeypatch.setattr(cli, "_build_parser", lambda: probe)
    monkeypatch.setattr(cli, "_OPTION_VERBS", {"out": frozenset({"eval"})})

    with pytest.raises(MQueryError) as caught:
        cli._refuse_irrelevant_options(
            argparse.Namespace(command="check"), ["check", "--out-file", "x"]
        )
    message = str(caught.value)
    assert "--out-file" in message, message
    assert "-o " not in message, message


def test_a_section_split_cannot_be_handed_a_parse_of_another_document() -> None:
    """Round 35's fix, and round 36's finding that it did not hold.

    Round 34 guarded the reused-parse split with
    `last_token_end > len(source)`. That catches a parse of a LONGER document
    and misses a shorter one: asked about `shared Alpha = 111;` with a parse of
    `shared Zed = 1;` it returned `{'Zed': 'shared Alpha = '}` - a member the
    document does not contain, with truncated text. A length inequality cannot
    be made exact, so round 35 replaced it with a paired object.

    Round 36: the pairing was a claim, not a property. `@dataclass(frozen=True)`
    generates a two-argument `__init__`, so the identical wrong result was
    still one call away - and now behind a PUBLIC constructor rather than a
    private helper.

    **And this test could not see it.** It asserted `members()`'s arity and
    the absence of the old helper; both stayed green while the defect was
    live, and its own name described something it never attempted. That is the
    regime error, in the guard written for a finding about the regime error.
    The first assertion below is the one that was red before `init=False` and
    green after - the others cannot distinguish the two states.
    """
    import inspect

    from pqtools import containers
    from pqtools.core import parse

    doc = "section S;\n\nshared Alpha = 111;\n\nshared Beta = 2;\n"
    other = "section S;\n\nshared Zed = 1;\n"

    # Round 37: `parse(other)` is hoisted OUT of the `with`, and the message is
    # pinned. Evaluated inside it, a `TypeError` from the Node bridge - the one
    # call in this test that leaves the process - satisfies the block without
    # the constructor ever being reached, which is the same "green for a reason
    # other than the one it names" shape this test exists to close. Measured: a
    # `parse` raising `TypeError` passed the bare version.
    foreign = parse(other)

    # The assertion that actually measures it: there is no second argument.
    with pytest.raises(TypeError, match=r"takes 2 positional arguments"):
        containers.ParsedSection(doc, foreign)  # type: ignore[call-arg]

    # Round 38: the `match=` above pins CPython's POSITIONAL-arity message, so
    # it survives the likeliest way this defect returns. Measured: adding a
    # keyword-only `parsed` parameter leaves the positional call raising the
    # same words - the assertion above stays GREEN - while
    # `ParsedSection(alpha, parsed=parse(zed))` rebuilds the round-35 mismatch
    # exactly. Nothing in the file constrained the constructor's parameter
    # list. This does.
    constructor = inspect.signature(containers.ParsedSection.__init__)
    assert list(constructor.parameters) == ["self", "section_source"], list(
        constructor.parameters
    )

    # Round 40: these two name the shape the assertions above depend on. Both
    # CAN go red - the `hasattr` if the old two-argument helper is
    # reintroduced, the signature pin if `members` gains a parameter - so the
    # earlier "on their own they prove nothing" undercounted them. What is
    # true is narrower: neither would have caught the round-35 defect, which
    # is why the constructor pin above exists.
    signature = inspect.signature(containers.ParsedSection.members)
    assert list(signature.parameters) == ["self"], list(signature.parameters)
    assert not hasattr(containers, "_split_shared_parsed")

    paired = containers.ParsedSection(doc)
    assert paired.source is doc
    assert paired.members() == containers.split_shared(doc)
    assert set(paired.members()) == {"Alpha", "Beta"}
    assert paired.members()["Alpha"] == "shared Alpha = 111;"


def test_option_presence_never_reports_a_positional() -> None:
    """Round 33's LOW: `set(vars(seen))` also returns the POSITIONAL dests.

    `_options_present` fed `command` and `file` into the refusal table
    alongside the real options. Nothing breaks today only because no
    `_OPTION_VERBS` key happens to be spelled like a positional - so an option
    later given `dest="file"`, or a positional later renamed to `out`, would
    make every verb refuse it and no test would notice. Correct by luck is not
    correct.
    """
    from pqtools.cli import _build_parser, _options_present

    parser = _build_parser()
    positionals = {
        action.dest
        for action in parser._actions  # noqa: SLF001 - argparse has no public API
        if not action.option_strings
    }
    # Vacuity: there must BE positionals for this to be measuring anything.
    assert positionals == {"command", "file"}, sorted(positionals)

    seen = _options_present(["add", "f.pbix", "--name", "X", "--source", "1"])
    assert not seen & positionals, sorted(seen & positionals)
    # ... and it still reports the options that were actually typed.
    assert seen == {"name", "source"}, sorted(seen)


def test_option_presence_refuses_rather_than_quietly_stopping_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 33's other LOW: the probe failed OPEN.

    `except SystemExit: return set()` reads downstream as "the user typed no
    options", which silently disables every refusal in
    `_refuse_irrelevant_options`. The main parse of the same tokens has
    already succeeded by then, so a probe failure is a pqtools bug - and a
    gate that stops gating without saying so is the exact failure this
    function was written to remove.
    """
    import argparse

    from pqtools import cli
    from pqtools.core import MQueryError

    broken = argparse.ArgumentParser(prog="pq")
    broken.add_argument("only_this", choices=["nope"])
    monkeypatch.setattr(cli, "_build_parser", lambda: broken)

    with pytest.raises(MQueryError) as caught:
        cli._options_present(["add", "f.pbix", "--name", "X"])
    assert "bug in pqtools" in str(caught.value)
    assert "Nothing was changed" in str(caught.value)


def test_every_documented_pq_command_uses_options_that_exist() -> None:
    """The round-32 MEDIUM: `llms.txt` told agents to run a command that cannot.

    `pq rename report.pq --from Old --to New` - `--from` is not an option
    (argparse exits 2 with a usage dump) and `--to` is refused on `rename`.
    That is the file written to be quoted VERBATIM by assistants, and nothing
    checked it, so the flag-refusal work of round 31b turned a doc example that
    merely ignored a flag into one that hard-fails.

    Deliberately narrow: it checks the FLAGS in each documented command, not
    whether the whole command would run. The docs are full of placeholders
    (`report.pq`, `FILE`, `'queries/**/*.pq'`) and fragments quoted mid-
    sentence (`pq check`), and a test that tried to execute them would fail on
    those rather than on a defect. Every flag naming an option that does not
    exist, or one this verb refuses, IS a defect - and it is the one that
    happened.
    """
    from pqtools.cli import _OPTION_VERBS, _build_parser

    parser = _build_parser()
    # Round 33: the dest was inferred from the flag TEXT (`--foo-bar` ->
    # `foo_bar`), the same text-inference the sibling test above abandoned. An
    # option declared with an explicit `dest=` would skip the "refused on this
    # verb" half of the check without ever failing. Ask argparse instead.
    dest_of = {
        option: action.dest
        for action in parser._actions  # noqa: SLF001 - argparse has no public API
        for option in action.option_strings
    }
    known = set(dest_of)
    verbs = {
        choice
        for action in parser._actions  # noqa: SLF001
        if action.dest == "command" and action.choices
        for choice in action.choices
    }
    root = Path(__file__).resolve().parent.parent

    def commands(text: str) -> list[str]:
        found = [match.group(1) for match in re.finditer(r"`(pq [^`\n]+)`", text)]
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("pq ") and "`" not in stripped:
                found.append(stripped.split("#")[0].strip().rstrip("\\").strip())
        return found

    problems: list[str] = []
    checked = 0
    for name in ("llms.txt", "README.md", "SUPPORT-MATRIX.md", "CLAUDE.md"):
        path = root / name
        if not path.exists():
            continue
        for command in commands(path.read_text(encoding="utf-8")):
            tokens = command.split()
            verb = next((t for t in tokens[1:] if not t.startswith("-")), None)
            if verb not in verbs:
                continue  # a fragment or a prose reference, not an invocation
            checked += 1
            for token in tokens:
                if not token.startswith("--"):
                    continue
                flag = token.split("=")[0]
                if flag not in known:
                    problems.append(f"{name}: {command} -> {flag} is not an option")
                    continue
                dest = dest_of[flag]
                if dest in _OPTION_VERBS and verb not in _OPTION_VERBS[dest]:
                    problems.append(f"{name}: {command} -> {flag} is refused on {verb}")
    assert not problems, problems
    # The scan must actually be finding commands; a broken extractor would
    # satisfy the assertion above by checking nothing at all.
    assert checked >= 50, checked


# --------------------------------------------------------------------------
# release_gate.sh step 9 - floor-log freshness
# --------------------------------------------------------------------------


def _freshness_repo(path: Path) -> Path:
    """A miniature repo the real freshness scripts can run inside.

    Both scripts resolve their own root with `git rev-parse --show-toplevel`
    and read the digest scope from `floor_digest.sh`, so the fixture has to
    supply that scope for real rather than stand in for it. The scripts under
    test are COPIED in, not reimplemented - the round-17 MEDIUM was a control
    that grepped the script instead of running it.
    """
    _repo(path)
    real = Path(__file__).resolve().parent.parent / "scripts"
    (path / "src").mkdir()
    (path / "tests").mkdir()
    (path / "evidence").mkdir()
    (path / "scripts").mkdir()
    (path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (path / "tests" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (path / "scripts" / "floor_venv_run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    for name in ("floor_digest.sh", "check_floor_freshness.sh"):
        target = path / "scripts" / name
        target.write_text((real / name).read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    _git(["add", "-A"], path)
    _git(["commit", "-qm", "fixture"], path)
    return path


def _freshness(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "scripts" / "check_floor_freshness.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_git_env(),
    )


def _write_log(repo: Path, name: str, digest: str) -> Path:
    """Write a floor log AND track it.

    Tracking is not incidental to the fixture - it is the contract. The
    freshness check resolves candidates with `git ls-files`, not a filesystem
    glob, precisely so that an untracked stray cannot decide what the gate
    certifies. A fixture that only wrote the file would be testing a
    resolution strategy the script no longer uses.
    """
    log = repo / "evidence" / name
    log.write_text(f"#   tree digest             : {digest}\n", encoding="utf-8")
    _git(["add", "--", f"evidence/{name}"], repo)
    return log


def _fixture_digest(repo: Path) -> str:
    return subprocess.run(
        ["bash", str(repo / "scripts" / "floor_digest.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=_git_env(),
    ).stdout.strip()


def test_the_freshness_check_fails_when_there_is_no_floor_log(tmp_path: Path) -> None:
    """The round-58 HIGH, and the reason this logic is its own script.

    Step 9 used to resolve one HARDCODED dated filename and, when that file
    was absent, print `SKIP  no floor log present` without touching the
    gate's failure counter - so a renamed, deleted, or simply dated-out log
    produced `GATE PASSED`, exit 0, with the evidence check never having run.
    The name carries the date the floor was produced, so the next floor run
    on any other day would have made that silent pass permanent.

    An absent artifact is the case this step exists for. It must fail.
    """
    repo = _freshness_repo(tmp_path / "repo")
    result = _freshness(repo)
    # 65 is the documented code for "no log". Asserting the code rather
    # than the prose: the message is allowed to improve, the contract the
    # gate reads is not.
    assert result.returncode == 65, result.stdout + result.stderr
    assert "floor log" in result.stderr, result.stderr


def test_the_freshness_check_refuses_when_two_floor_logs_are_present(
    tmp_path: Path,
) -> None:
    """Resolving by glob replaced a hardcoded name, which introduces the
    opposite failure: two logs and no way to know which one the gate is
    certifying. Picking either silently would let a stale log be shadowed by
    a fresh one, or the reverse."""
    repo = _freshness_repo(tmp_path / "repo")
    digest = _fixture_digest(repo)
    _write_log(repo, "floor-venv-suite-2026-01-01.log", digest)
    _write_log(repo, "floor-venv-suite-2026-02-02.log", digest)
    result = _freshness(repo)
    assert result.returncode == 66, result.stdout + result.stderr
    assert "more than one" in result.stderr, result.stderr


def test_the_freshness_check_fails_on_a_stale_digest(tmp_path: Path) -> None:
    """The case step 9 was written for: the log describes a tree that is no
    longer the tree on disk."""
    repo = _freshness_repo(tmp_path / "repo")
    _write_log(repo, "floor-venv-suite-2026-01-01.log", "0" * 64)
    result = _freshness(repo)
    assert result.returncode == 67, result.stdout + result.stderr
    assert "is stale" in result.stderr, result.stderr


def test_the_freshness_check_passes_on_a_current_log(tmp_path: Path) -> None:
    """The positive control for the three refusals above.

    Without it, a script that failed unconditionally would satisfy every
    other test in this group.
    """
    repo = _freshness_repo(tmp_path / "repo")
    log = _write_log(repo, "floor-venv-suite-2026-01-01.log", _fixture_digest(repo))
    result = _freshness(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.name in result.stdout, result.stdout


def test_the_digest_refuses_an_untracked_file_in_its_own_scope(
    tmp_path: Path,
) -> None:
    """Untracked files used to be hashed IN (`git ls-files -co`), which made
    the digest depend on the developer's private working files: a value no
    clone, CI run, or review worktree could reproduce, and one that step 9's
    printed remedy - re-run the floor - cannot fix, because the untracked
    file is still there afterwards."""
    repo = _freshness_repo(tmp_path / "repo")
    (repo / "src" / "private_scratch.py").write_text("y = 2\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "floor_digest.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_git_env(),
    )
    assert result.returncode != 0, result.stdout
    assert "untracked file(s) in scope" in result.stderr, result.stderr
    assert "private_scratch.py" in result.stderr, result.stderr


def test_the_digest_file_count_agrees_with_what_the_digest_hashed(
    tmp_path: Path,
) -> None:
    """`--files` is a claim printed beside the digest, so it needs its own
    control.

    The obvious NUL-aware count, `awk 'BEGIN { RS = "\\0" }'`, is wrong on
    macOS: awk reads "\\0" as the empty string and switches to paragraph
    mode, so the whole NUL-separated list becomes ONE record. It reported
    `1` for a 125-file scope while the digest printed on the line above was
    computed over all 125.
    """
    repo = _freshness_repo(tmp_path / "repo")
    reported = subprocess.run(
        ["bash", str(repo / "scripts" / "floor_digest.sh"), "--files"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=_git_env(),
    ).stdout.strip()
    tracked = _git(
        [
            "ls-files",
            "--",
            "src",
            "tests",
            "pyproject.toml",
            "scripts/floor_venv_run.sh",
            "scripts/floor_digest.sh",
        ],
        repo,
    )
    expected = len(tracked.strip().splitlines())
    # The fixture is small - the real scope is ~125 files, this one a handful -
    # and paragraph mode collapses ANY number of records to 1. So the bug is
    # only visible while the fixture holds more than one file. If a future edit
    # shrinks it to a single file, this fails loudly rather than passing while
    # proving nothing.
    assert expected > 1, f"fixture scope collapsed to {expected} file(s)"
    assert int(reported) == expected, reported


def test_every_branch_of_the_gates_floor_step_reaches_the_failure_counter(
    tmp_path: Path,
) -> None:
    """The specific shape of the round-58 HIGH, pinned.

    This one is structural rather than behavioural, and deliberately so: the
    branch it guards lives inside `release_gate.sh`, which runs the whole
    suite before it gets there, so exercising it costs ~12 minutes. What
    made the defect possible was not the freshness logic - that is now its
    own script, tested above - but a branch in the gate that printed a
    message and returned without touching `FAILED`. So this asserts the
    property that was violated: the floor step has no arm that only prints.
    """
    gate = Path(__file__).resolve().parent.parent / "scripts" / "release_gate.sh"
    text = gate.read_text(encoding="utf-8")
    assert "check_floor_freshness.sh" in text, "step 9 no longer calls the script"
    # Anchored to the INVOCATION, not to the first mention of the name: the
    # comment above the step names the script and quotes "GATE PASSED", so
    # the obvious `text.index(name)` sliced the comment and asserted against
    # prose. Caught by this test failing on its own first run.
    start = text.index("if FRESHNESS=")
    step = text[start : text.index("GATE PASSED", start)]
    assert "check 0 " in step and "check 1 " in step, step
    # The exact wording the defect wore. If a SKIP arm is ever reintroduced
    # for a missing artifact, it fails here.
    assert "SKIP" not in step, step
