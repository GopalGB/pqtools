"""The four new CLI verbs from PRD-pandas-for-powerquery-2026-09-06.md: `show`,
`explain`, `diff`, glob/batch mode on the read-only verbs, and `eval
--set-param`.

`tests/test_cli.py` and `tests/test_container_workflow.py` already pin every
existing invocation; this file only owns behaviour that did not exist before.
Positive controls for the four most load-bearing guarantees (show never
evaluates, an empty glob is a typed error not a silent success, one exit code
covers the whole batch, and a bad --set-param value names the parameter and
the text) are recorded in the task report, not here - they are performed by
temporarily reintroducing each defect in `src/pqtools/cli.py`, confirming the
relevant test below goes red, then reverting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pqtools.cli import main


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# show
# --------------------------------------------------------------------------


def test_show_prints_the_whole_files_source(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "q.pq", "let A = 1 + 1 in A")
    assert main(["show", str(path)]) == 0
    assert capsys.readouterr().out == "let A = 1 + 1 in A\n"


def test_show_member_prints_the_raw_expression_not_its_value(
    tmp_path: Path, capsys
) -> None:
    # If `show` evaluated, this would print 2 - it must print the text.
    path = _write(
        tmp_path / "section.pq",
        'section S; shared A = 1 + 1; shared B = "hi";',
    )
    assert main(["show", str(path), "--member", "A"]) == 0
    assert capsys.readouterr().out.strip() == "1 + 1"


def test_show_never_evaluates_a_query_that_would_fail_if_run(
    tmp_path: Path, capsys
) -> None:
    """The entire point of the verb. `File.Contents` on a path that does not
    exist fails the moment the connector runs - `show` must still print the
    source, because it never reaches the connector at all.
    """
    path = _write(
        tmp_path / "q.pq",
        'Csv.Document(File.Contents("does-not-exist.csv"))',
    )
    assert main(["show", str(path)]) == 0
    out = capsys.readouterr().out
    assert "does-not-exist.csv" in out
    assert "Csv.Document" in out


def test_show_json_wraps_the_source_string(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "q.pq", "1 + 1")
    assert main(["show", str(path), "--json"]) == 0
    assert capsys.readouterr().out.strip() == '"1 + 1"'


def test_show_missing_member_is_a_typed_error(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "section.pq", "section S; shared A = 1;")
    assert main(["show", str(path), "--member", "Nope"]) == 2
    assert "no shared member named" in capsys.readouterr().err


# --------------------------------------------------------------------------
# explain
# --------------------------------------------------------------------------


def test_explain_reports_an_implemented_name_as_supported(capsys) -> None:
    assert main(["explain", "Table.SelectRows"]) == 0
    out = capsys.readouterr().out
    assert "Table.SelectRows" in out
    assert "implemented" in out.lower()


def test_explain_reports_a_documented_but_unimplemented_connector(capsys) -> None:
    # Access.Database is real (Microsoft documents it) but needs a driver
    # pqtools does not ship - catalog.py's own reason for refusing it.
    assert main(["explain", "Access.Database"]) == 0
    out = capsys.readouterr().out
    assert "connector" in out.lower()
    assert "--bind" in out


def test_explain_is_honest_about_a_name_it_does_not_recognize(capsys) -> None:
    assert main(["explain", "Totally.Bogus.Name"]) == 0
    out = capsys.readouterr().out
    assert "Totally.Bogus.Name" in out
    assert "typo" in out.lower()


def test_explain_json_carries_a_supported_boolean(capsys) -> None:
    assert main(["explain", "Table.SelectRows", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "name": "Table.SelectRows",
        "supported": True,
        "message": payload["message"],
    }
    assert "implemented" in payload["message"].lower()


def test_explain_json_supported_is_false_for_a_refused_name(capsys) -> None:
    assert main(["explain", "Access.Database", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is False


def test_explain_requires_a_name(capsys) -> None:
    assert main(["explain"]) == 2
    assert "NAME" in capsys.readouterr().err


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def test_diff_identical_files_exit_zero_with_no_output(tmp_path: Path, capsys) -> None:
    a = _write(tmp_path / "a.pq", "let A = 1 + 1 in A")
    b = _write(tmp_path / "b.pq", "let A = 1 + 1 in A")
    assert main(["diff", str(a), str(b)]) == 0
    assert capsys.readouterr().out == ""


def test_diff_compares_formatted_source_so_whitespace_only_edits_vanish(
    tmp_path: Path, capsys
) -> None:
    """The documented choice (see `pq --help`): diff normalises formatting
    first, so a rename that only reflows spacing does not drown a real
    change in a wall of formatting noise.
    """
    a = _write(tmp_path / "a.pq", "let A=1+1 in A")
    b = _write(tmp_path / "b.pq", "let   A  =  1 + 1   in   A")
    assert main(["diff", str(a), str(b)]) == 0
    assert capsys.readouterr().out == ""


def test_diff_reports_a_real_difference_with_exit_code_one(
    tmp_path: Path, capsys
) -> None:
    a = _write(tmp_path / "a.pq", "let A = 1 + 1 in A")
    b = _write(tmp_path / "b.pq", "let A = 1 + 2 in A")
    assert main(["diff", str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "-let A = 1 + 1 in A" in out
    assert "+let A = 1 + 2 in A" in out
    assert str(a) in out
    assert str(b) in out


def test_diff_json_reports_the_identical_flag_and_the_diff_text(
    tmp_path: Path, capsys
) -> None:
    a = _write(tmp_path / "a.pq", "1")
    b = _write(tmp_path / "b.pq", "2")
    assert main(["diff", str(a), str(b), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["identical"] is False
    assert "-1" in payload["diff"]
    assert "+2" in payload["diff"]


def test_diff_on_a_missing_file_is_a_typed_error_not_a_bare_traceback(
    tmp_path: Path, capsys
) -> None:
    # `_source`/`_snapshot` (core.py, not this lane) report every unreadable
    # path with the same generic SAFE_WRITE_REFUSED message regardless of
    # cause - already true for every other verb (`pq eval missing.pq` gives
    # the identical text). `diff` inherits that by reusing `_source`, so the
    # property this test can pin is "typed error, not a crash", not the
    # specific wording.
    a = _write(tmp_path / "a.pq", "1")
    assert main(["diff", str(a), str(tmp_path / "missing.pq")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error ")
    assert "Traceback" not in err


# --------------------------------------------------------------------------
# glob / batch
# --------------------------------------------------------------------------


def test_check_accepts_two_explicit_files_one_line_each(tmp_path: Path, capsys) -> None:
    good = _write(tmp_path / "good.pq", "let A = 1 + 1 in A")
    bad = _write(tmp_path / "bad.pq", "let A = 1 +")
    assert main(["check", str(good), str(bad)]) == 2
    out = capsys.readouterr().out
    # A parse failure is a DIAGNOSTIC - check() converts it, it never raises
    # - so it prints on stdout in the same "file:line:col: severity code:
    # message" shape every other check() diagnostic uses, not to stderr.
    assert "good.pq: OK" in out
    assert "bad.pq" in out
    assert "error" in out


def test_check_accepts_a_glob_and_finds_every_match(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "a.pq", "let A = 1 + 1 in A")
    _write(tmp_path / "b.pq", "let A = 1 + 1 in A")
    assert main(["check", str(tmp_path / "*.pq")]) == 0
    out = capsys.readouterr().out
    assert out.count("OK") == 2


def test_check_batch_exit_code_is_the_worst_of_any_file(tmp_path: Path, capsys) -> None:
    """ONE exit code for the whole batch - a passing file must not hide a
    failing one, and a failing file must not hide the passing ones' output.
    """
    _write(tmp_path / "a.pq", "let A = 1 + 1 in A")
    _write(tmp_path / "b.pq", "let A = 1 +")  # parse error
    _write(tmp_path / "c.pq", "let A = 1 + 1 in A")
    assert main(["check", str(tmp_path / "*.pq")]) == 2
    out = capsys.readouterr().out
    assert out.count("OK") == 2
    assert "b.pq" in out


def test_check_batch_reports_an_unreadable_file_without_losing_the_others(
    tmp_path: Path, capsys
) -> None:
    """The OTHER failure branch: not a parse-error diagnostic (check()
    converts those itself, see above) but a file that cannot even be read
    - a name check() never gets the chance to run on. This exercises the
    try/except around `_check_diagnostics`, not the severity check inside
    the diagnostics it returns.
    """
    good = _write(tmp_path / "good.pq", "let A = 1 + 1 in A")
    missing = tmp_path / "missing.pq"
    assert main(["check", str(good), str(missing)]) == 2
    out, err = capsys.readouterr()
    assert "good.pq: OK" in out
    assert "missing.pq" in err
    assert "error" in err


def test_glob_matching_nothing_is_a_typed_error_not_a_silent_success(
    tmp_path: Path, capsys
) -> None:
    pattern = str(tmp_path / "nope-*.pq")
    assert main(["check", pattern]) == 2
    err = capsys.readouterr().err
    assert "no files matched glob" in err
    assert pattern in err


def test_parse_batch_is_json_and_names_each_file(tmp_path: Path, capsys) -> None:
    a = _write(tmp_path / "a.pq", "1")
    b = _write(tmp_path / "b.pq", "2")
    assert main(["parse", str(a), str(b)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {item["file"] for item in payload} == {str(a), str(b)}


def test_parse_batch_reports_a_bad_file_without_losing_the_good_ones(
    tmp_path: Path, capsys
) -> None:
    good = _write(tmp_path / "good.pq", "1")
    bad = _write(tmp_path / "bad.pq", "let A = 1 +")
    assert main(["parse", str(good), str(bad)]) == 2
    payload = json.loads(capsys.readouterr().out)
    by_file = {item["file"]: item for item in payload}
    assert "parsed" in by_file[str(good)]
    assert "error" in by_file[str(bad)]


def test_dependencies_batch_names_each_file(tmp_path: Path, capsys) -> None:
    a = _write(tmp_path / "a.pq", "Number.From(1)")
    b = _write(tmp_path / "b.pq", "Text.From(1)")
    assert main(["dependencies", str(a), str(b)]) == 0
    payload = json.loads(capsys.readouterr().out)
    by_file = {item["file"]: item["dependencies"] for item in payload}
    assert by_file[str(a)] == ["Number.From"]
    assert by_file[str(b)] == ["Text.From"]


def test_format_batch_previews_a_diff_per_file_and_writes_nothing(
    tmp_path: Path, capsys
) -> None:
    a = _write(tmp_path / "a.pq", "let A=1+1 in A")
    b = _write(tmp_path / "b.pq", "let B=2+2 in B")
    assert main(["format", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "---- " in out
    assert str(a) in out
    assert str(b) in out
    assert a.read_text() == "let A=1+1 in A"
    assert b.read_text() == "let B=2+2 in B"


def test_format_write_refuses_a_multi_file_batch_by_name(
    tmp_path: Path, capsys
) -> None:
    a = _write(tmp_path / "a.pq", "let A=1+1 in A")
    b = _write(tmp_path / "b.pq", "let B=2+2 in B")
    assert main(["format", str(a), str(b), "--write"]) == 2
    err = capsys.readouterr().err
    assert "single-file" in err
    # Refused BY NAME, not half-applied: neither file is touched.
    assert a.read_text() == "let A=1+1 in A"
    assert b.read_text() == "let B=2+2 in B"


def test_format_write_still_works_for_one_file_resolved_from_a_glob(
    tmp_path: Path, capsys
) -> None:
    a = _write(tmp_path / "solo.pq", "let A=1+1 in A")
    assert main(["format", str(tmp_path / "solo*.pq"), "--write"]) == 0
    assert "A = 1 + 1" in a.read_text()


def test_list_accepts_multiple_files(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "s1.pq", "section S1; shared Alpha = 1;")
    _write(tmp_path / "s2.pq", "section S2; shared Beta = 2;")
    assert main(["list", str(tmp_path / "s1.pq"), str(tmp_path / "s2.pq")]) == 0
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "Beta" in out


def test_show_accepts_a_glob_and_headers_each_file(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "a.pq", "1")
    _write(tmp_path / "b.pq", "2")
    assert main(["show", str(tmp_path / "*.pq")]) == 0
    out = capsys.readouterr().out
    assert "---- " in out
    assert "1" in out
    assert "2" in out


def test_rename_refuses_a_second_file_argument(tmp_path: Path) -> None:
    """Writing verbs stay single-file - argparse itself refuses the extra
    positional, by name, rather than silently renaming only the first file.
    """
    a = _write(tmp_path / "a.pq", "let A = 1 in A")
    b = _write(tmp_path / "b.pq", "let A = 1 in A")
    with pytest.raises(SystemExit):
        main(["rename", str(a), str(b), "--old", "A", "--new", "B"])


# --------------------------------------------------------------------------
# eval --set-param
# --------------------------------------------------------------------------


def test_eval_set_param_binds_a_date_literal_through_the_m_evaluator(
    tmp_path: Path, capsys
) -> None:
    path = _write(tmp_path / "q.pq", "StartDate")
    assert main(["eval", str(path), "--set-param", "StartDate=#date(2024,1,1)"]) == 0
    assert capsys.readouterr().out.strip() == '"2024-01-01"'


def test_eval_set_param_binds_a_string_literal(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "q.pq", "Region")
    assert main(["eval", str(path), "--set-param", 'Region="EU"']) == 0
    assert capsys.readouterr().out.strip() == '"EU"'


def test_eval_set_param_binds_a_number_literal(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "q.pq", "Limit")
    assert main(["eval", str(path), "--set-param", "Limit=100"]) == 0
    assert capsys.readouterr().out.strip() == "100"


def test_eval_set_param_is_repeatable(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "q.pq", "Limit + Offset")
    assert (
        main(
            [
                "eval",
                str(path),
                "--set-param",
                "Limit=100",
                "--set-param",
                "Offset=5",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "105"


def test_eval_set_param_wins_over_a_bind_on_the_same_name(
    tmp_path: Path, capsys
) -> None:
    query = _write(tmp_path / "q.pq", "Source")
    data = _write(tmp_path / "data.json", "1")
    assert (
        main(
            [
                "eval",
                str(query),
                "--bind",
                f"Source={data}",
                "--set-param",
                "Source=999",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "999"


def test_eval_set_param_requires_name_equals_value(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "q.pq", "1")
    assert main(["eval", str(path), "--set-param", "no-equals-sign"]) == 2
    assert "NAME=VALUE" in capsys.readouterr().err


def test_eval_set_param_parse_failure_names_the_parameter_and_the_text(
    tmp_path: Path, capsys
) -> None:
    path = _write(tmp_path / "q.pq", "1")
    assert main(["eval", str(path), "--set-param", "StartDate=this is not m"]) == 2
    err = capsys.readouterr().err
    assert "StartDate" in err
    assert "this is not m" in err


def test_eval_set_param_never_uses_python_eval(tmp_path: Path, capsys) -> None:
    """A Python `eval()` fallback would execute this; the M parser refuses it
    as invalid M source instead.
    """
    path = _write(tmp_path / "q.pq", "1")
    assert main(["eval", str(path), "--set-param", "X=__import__('os').getcwd()"]) == 2
    assert "not a valid M value" in capsys.readouterr().err
