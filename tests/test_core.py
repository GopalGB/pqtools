import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mquery_toolkit import core
from mquery_toolkit.core import (
    MAX_BYTES,
    MQueryError,
    NodeError,
    ParseError,
    RenameRefusal,
    SafeWriteError,
    _ProcessOutputLimit,
    _run_process_bounded,
    check,
    dependencies,
    format_source,
    parse,
    rename,
    replace_source,
    update_file,
)

SOURCE = "let A = Number.From(1), B = A // A stays a comment\nin B"


def test_pinned_bridge_returns_tokens():
    parsed = parse(SOURCE)
    assert parsed["rootKind"] == "LetExpression"
    assert [item["text"] for item in parsed["tokens"]].count("A") == 2


def test_format_is_idempotent_and_preserves_crlf_without_final_newline():
    source = "let A=1 in A\r\n"
    formatted = format_source(source)
    assert "\r\n" in formatted
    assert format_source(formatted) == formatted
    assert not format_source("let A=1 in A").endswith("\n")


def test_parse_error_is_deterministic():
    with pytest.raises(ParseError, match="parse error at"):
        parse("let =")
    assert check("let =")[0].code == "M_PARSE_ERROR"


def test_check_frozen_rules_and_positions():
    source = (
        "let A = 1, A = 2, Dead = 3, "
        'Source = Web.Contents(Url), Password = "secret" in Missing'
    )
    diagnostics = check(source, "query.pq")
    codes = {item.code for item in diagnostics}
    assert {"M001", "M002", "M003", "M004", "M005", "M006"} <= codes
    assert all(
        item.file == "query.pq" and item.line >= 1 and item.column >= 1
        for item in diagnostics
    )


def test_check_literal_web_url_is_not_dynamic():
    diagnostics = check('let A = Web.Contents(  "https://example.test") in A')
    assert "M002" not in {item.code for item in diagnostics}


def test_check_reports_every_dynamic_web_contents_and_credential_literal():
    source = (
        'let A = Web.Contents("https://example.test"), B = Web.Contents(Url), '
        'C = [Password = "a", Token = "b"] in B'
    )
    diagnostics = check(source)
    first_call = source.index("Web.Contents(")
    first_call_column = first_call - source.rfind("\n", 0, first_call)
    m002 = [item for item in diagnostics if item.code == "M002"]
    m003 = [item for item in diagnostics if item.code == "M003"]
    assert len(m002) == 1
    assert m002[0].column > first_call_column
    assert len(m003) == 2


def test_check_understands_function_and_each_scopes():
    function_codes = {item.code for item in check("let F = (x) => x in F")}
    each_codes = {
        item.code for item in check("let A = List.Transform({1}, each _ + 1) in A")
    }
    assert "M005" not in function_codes
    assert "M005" not in each_codes


def test_dependencies_only_returns_invoked_names():
    assert dependencies(SOURCE) == ["Number.From"]
    assert dependencies("let F = (x) => x, A = F(1) in A") == []


def test_rename_updates_binding_references_but_not_comment_or_string():
    updated = rename(SOURCE + '\n// "A"', "A", "Renamed")
    assert "Renamed =" in updated and "B = Renamed" in updated
    assert "// A stays" in updated and '"A"' in updated


def test_rename_handles_multiline_equality_and_rejects_collisions_and_keywords():
    source = "let\n A = 1,\n B = if A = 1 then A else 0\nin B"
    assert "if Renamed = 1 then Renamed" in rename(source, "A", "Renamed")
    with pytest.raises(RenameRefusal, match="collides"):
        rename(source, "A", "B")
    with pytest.raises(RenameRefusal, match="reserved"):
        rename(source, "A", "in")


def test_bridge_normalizes_invalid_unicode():
    with pytest.raises(MQueryError, match="UTF-8"):
        parse("\ud800")


@pytest.mark.parametrize(
    "source",
    [
        'let #"A B" = 1 in #"A B"',
        "let A = [A = 1] in A",
        "let A = () => A in A",
    ],
)
def test_rename_refuses_unsafe_shapes(source):
    with pytest.raises(RenameRefusal):
        rename(source, "A", "B")


def test_replace_source_validates_complete_replacement():
    assert replace_source("let A = 1 in A", "let B = 2 in B") == "let B = 2 in B"
    with pytest.raises(ParseError):
        replace_source("let A = 1 in A", "let =")


def test_input_limit():
    with pytest.raises(MQueryError, match="10 MiB"):
        parse("x" * (MAX_BYTES + 1))


def test_process_output_is_terminated_at_limit():
    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 11000000)"]
    with pytest.raises(_ProcessOutputLimit):
        _run_process_bounded(command, None, 10)


def test_process_input_write_obeys_same_timeout():
    command = [sys.executable, "-c", "import time; time.sleep(2)"]
    with pytest.raises(subprocess.TimeoutExpired):
        _run_process_bounded(command, b"x" * MAX_BYTES, 1)


def test_dry_run_and_atomic_write_preserve_mode_and_no_partial(tmp_path: Path):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A", encoding="utf-8")
    path.chmod(0o640)
    assert update_file(path, format_source).startswith("---")
    assert path.read_text() == "let A=1 in A"
    update_file(path, format_source, write=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert path.read_text() == "let A = 1 in A"


def test_write_refuses_symlink_and_hardlink(tmp_path: Path):
    original = tmp_path / "query.pq"
    original.write_text("let A=1 in A")
    hard = tmp_path / "hard.pq"
    os.link(original, hard)
    with pytest.raises(SafeWriteError, match="single-link"):
        update_file(original, format_source, write=True)
    link = tmp_path / "link.pq"
    link.symlink_to(original)
    with pytest.raises(SafeWriteError, match="non-symlink"):
        update_file(link, format_source, write=True)


def test_write_refuses_concurrent_change(tmp_path: Path):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A")

    def change_then_format(source: str) -> str:
        path.write_text("let A=2 in A")
        return format_source(source)

    with pytest.raises(SafeWriteError, match="changed"):
        update_file(path, change_then_format, write=True)


def test_node_version_gate_accepts_22_and_newer(monkeypatch):
    cases = [
        ("node-a", b"v22.23.2\n", True),
        ("node-b", b"v24.1.0\n", True),
        ("node-c", b"v26.0.0\n", True),
        ("node-d", b"v20.19.0\n", False),
        ("node-e", b"garbage\n", False),
    ]
    for binary, stdout, ok in cases:
        core._require_node.cache_clear()
        monkeypatch.setattr(
            core,
            "_run_process_bounded",
            lambda command, input_data, timeout, stdout=stdout: (
                subprocess.CompletedProcess(command, 0, stdout, b"")
            ),
        )
        if ok:
            core._require_node(binary)
        else:
            with pytest.raises(NodeError):
                core._require_node(binary)
    core._require_node.cache_clear()


def test_update_file_leaves_no_lock_or_temp_files(tmp_path: Path):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A", encoding="utf-8")
    update_file(path, format_source)
    assert {item.name for item in tmp_path.iterdir()} == {"query.pq"}
    update_file(path, format_source, write=True)
    assert {item.name for item in tmp_path.iterdir()} == {"query.pq"}
    assert path.read_text() == "let A = 1 in A"
