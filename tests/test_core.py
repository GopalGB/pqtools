import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pqtools import core
from pqtools.core import (
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


def test_check_ignores_comments_and_strings():
    commented = 'let A = 1 in A // Web.Contents(Url) Password = "x"'
    codes = {item.code for item in check(commented)}
    assert "M002" not in codes
    assert "M003" not in codes
    assert "M002" not in {
        item.code for item in check('let A = "Web.Contents(Url)" in A')
    }


def test_check_bom_offsets_match_the_no_bom_source():
    with_bom = check("\ufefflet A = Web.Contents(Url) in A")
    without_bom = check("let A = Web.Contents(Url) in A")
    m002_with_bom = [item for item in with_bom if item.code == "M002"]
    m002_without_bom = [item for item in without_bom if item.code == "M002"]
    assert len(m002_with_bom) == 1
    assert m002_with_bom[0].column == 9
    assert m002_without_bom[0].column == 9


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


def test_check_parses_the_source_once(monkeypatch):
    calls = []
    original = core._bridge

    def counting_bridge(source, kind, **options):
        calls.append(kind)
        return original(source, kind, **options)

    monkeypatch.setattr(core, "_bridge", counting_bridge)
    check("let A = Web.Contents(Url) in A")
    assert calls == ["parse"]


def test_rename_uses_two_bridge_calls(monkeypatch):
    calls = []
    original = core._bridge

    def counting_bridge(source, kind, **options):
        calls.append(kind)
        return original(source, kind, **options)

    monkeypatch.setattr(core, "_bridge", counting_bridge)
    rename(SOURCE, "A", "Renamed")
    assert calls == ["rename", "parse"]


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


def test_rename_refuses_to_capture_a_free_identifier():
    with pytest.raises(RenameRefusal, match="already appears"):
        rename("let A = 1 in A + Total", "A", "Total")
    assert (
        rename("let A = 1 in A + B", "A", "Renamed") == "let Renamed = 1 in Renamed + B"
    )


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


def test_replace_source_rejects_invalid_unicode():
    with pytest.raises(MQueryError, match="UTF-8"):
        replace_source("let A = 1 in A", "\udcff")


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


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "POSIX-only guarantee: closing the pipe fd unblocks the abandoned reader. "
        "On Windows a thread already blocked in ReadFile is not released by a close, "
        "so the call can run until the grandchild exits. Documented in README Limits."
    ),
)
def test_process_timeout_survives_grandchild_holding_stdout():
    command = [
        sys.executable,
        "-c",
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "sys.stdout.write('parent done')",
    ]
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run_process_bounded(command, None, 2)
    assert time.monotonic() - start < 10


@pytest.mark.skipif(
    not Path("/dev/fd").exists(), reason="/dev/fd not available on this platform"
)
def test_process_timeout_closes_abandoned_pipe_fds():
    command = [
        sys.executable,
        "-c",
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "sys.stdout.write('parent done')",
    ]
    before = len(os.listdir("/dev/fd"))
    with pytest.raises(subprocess.TimeoutExpired):
        _run_process_bounded(command, None, 2)
    after = len(os.listdir("/dev/fd"))
    assert after <= before + 1


def test_dry_run_and_atomic_write_preserve_mode_and_no_partial(tmp_path: Path):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A", encoding="utf-8")
    path.chmod(0o640)
    assert update_file(path, format_source).startswith("---")
    assert path.read_text() == "let A=1 in A"
    update_file(path, format_source, write=True)
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert path.read_text() == "let A = 1 in A"


def test_format_preserves_final_newline_state(tmp_path: Path):
    with_newline = tmp_path / "with_newline.pq"
    with_newline.write_text("let A=1 in A\n", encoding="utf-8")
    update_file(with_newline, format_source, write=True)
    content = with_newline.read_text()
    assert content.endswith("\n") and not content.endswith("\n\n")

    without_newline = tmp_path / "without_newline.pq"
    without_newline.write_text("let A=1 in A", encoding="utf-8")
    update_file(without_newline, format_source, write=True)
    assert not without_newline.read_text().endswith("\n")


def test_bom_survives_format_and_rename(tmp_path: Path):
    fixture = (
        Path(__file__).parent / "fixtures" / "DataConnectors" / "HelloWorld.query.pq"
    )
    path = tmp_path / fixture.name
    shutil.copy(fixture, path)
    update_file(path, format_source, write=True)
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    parse(data.decode("utf-8"))
    assert update_file(path, format_source, write=True) == ""


def test_bom_rename_shifts_spans_correctly():
    assert (
        rename("\ufefflet A = 1 in A", "A", "Renamed")
        == "\ufefflet Renamed = 1 in Renamed"
    )


def test_bom_check_returns_no_parse_error():
    diagnostics = check("\ufefflet A = 1 in A")
    assert not any(item.code == "M_PARSE_ERROR" for item in diagnostics)


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX link semantics; Windows symlinks need Developer Mode",
)
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
    with pytest.raises(SafeWriteError, match="non-symlink"):
        update_file(link, format_source)


def test_write_refuses_concurrent_change(tmp_path: Path):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A")

    def change_then_format(source: str) -> str:
        path.write_text("let A=2 in A")
        return format_source(source)

    with pytest.raises(SafeWriteError, match="changed"):
        update_file(path, change_then_format, write=True)


def test_write_lock_failure_closes_descriptor(tmp_path: Path, monkeypatch):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A", encoding="utf-8")

    def fail_lock(descriptor: int) -> None:
        raise OSError("simulated lock failure")

    monkeypatch.setattr(core, "_lock_file", fail_lock)
    with pytest.raises(SafeWriteError, match="lock"):
        update_file(path, format_source, write=True)
    assert {item.name for item in tmp_path.iterdir()} == {"query.pq"}
    assert path.read_text() == "let A=1 in A"


def test_node_version_gate_accepts_22_and_newer(monkeypatch):
    cases = [
        ("node-a", b"v22.23.2\n", True),
        ("node-b", b"v24.1.0\n", True),
        ("node-c", b"v26.0.0\n", True),
        ("node-d", b"v20.19.0\n", False),
        ("node-e", b"garbage\n", False),
        ("node-f", b"v100.0.0\n", True),
        ("node-g", b"v22.0.0-nightly20260101\n", True),
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


def test_node_binary_requires_env_or_path(monkeypatch):
    monkeypatch.delenv("MQUERY_NODE", raising=False)
    # _node_binary passes path= on Windows, so the stub must accept kwargs.
    monkeypatch.setattr(core.shutil, "which", lambda *_args, **_kwargs: None)
    with pytest.raises(NodeError):
        core._node_binary()


def test_node_binary_refuses_cwd_resolution(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda *a, **k: str(tmp_path / "node"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MQUERY_NODE", raising=False)
    with pytest.raises(NodeError, match="current directory"):
        core._node_binary()


def test_update_file_fsyncs_parent_directory_after_replace(tmp_path: Path, monkeypatch):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A", encoding="utf-8")
    real_fsync = os.fsync
    calls = []

    def recording_fsync(fd):
        info = os.fstat(fd)
        calls.append(stat.S_ISDIR(info.st_mode))
        real_fsync(fd)

    monkeypatch.setattr(core.os, "fsync", recording_fsync)
    update_file(path, format_source, write=True)
    assert calls, "the file itself must always be fsynced"
    if os.name == "nt":
        # Windows cannot open a directory to fsync it; update_file suppresses that
        # OSError by design, so only the file fsync is observable here.
        assert not any(calls)
    else:
        assert len(calls) >= 2
        assert any(calls), "the parent directory must be fsynced after os.replace"


def test_update_file_leaves_no_lock_or_temp_files(tmp_path: Path):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A", encoding="utf-8")
    update_file(path, format_source)
    assert {item.name for item in tmp_path.iterdir()} == {"query.pq"}
    update_file(path, format_source, write=True)
    assert {item.name for item in tmp_path.iterdir()} == {"query.pq"}
    assert path.read_text() == "let A = 1 in A"


def test_write_does_not_clobber_stale_temp(tmp_path: Path):
    path = tmp_path / "query.pq"
    path.write_text("let A=1 in A", encoding="utf-8")
    stale = tmp_path / f".query.pq.{os.getpid()}.tmp"
    stale.write_text("stale", encoding="utf-8")
    update_file(path, format_source, write=True)
    assert stale.read_text() == "stale"
    assert path.read_text() == "let A = 1 in A"
    assert {item.name for item in tmp_path.iterdir()} == {
        "query.pq",
        f".query.pq.{os.getpid()}.tmp",
    }
