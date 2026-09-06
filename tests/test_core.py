import ast
import inspect
import os
import shutil
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

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
    _ProcessReadError,
    _ProcessWriteError,
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


class _ReadFailsMidStream:
    """A stdout wrapper that yields one chunk, then fails like a broken pipe."""

    def __init__(self, stream):
        self._stream = stream
        self._calls = 0

    def read(self, size):
        self._calls += 1
        if self._calls == 1:
            return b'{"partial": '
        raise OSError(5, "Input/output error")

    def fileno(self):
        return self._stream.fileno()

    def close(self):
        return self._stream.close()


@pytest.fixture
def failing_stdout(monkeypatch):
    """Make the real child's stdout fail after one chunk.

    Used by exactly one test. Replacing `subprocess.Popen` is global for the
    duration, so the narrowest seam that proves a given claim is preferred
    everywhere else in this file - three sibling tests patch the runner
    directly instead.
    """
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        process.stdout = _ReadFailsMidStream(process.stdout)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)


def test_process_read_failure_is_raised_not_returned_as_short_output(failing_stdout):
    # The buffer left behind is a valid prefix and carries no mark of the
    # failure, so returning it would be a silent truncation.
    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 64)"]
    with pytest.raises(_ProcessReadError):
        _run_process_bounded(command, None, 10)


def test_pqtest_refuses_a_truncated_read_rather_than_returning_it(monkeypatch):
    # This is the path with no structural check on the output: `_run_bounded`
    # decodes and returns it, so nothing downstream would reject a prefix.
    # `pqtest` imported the runner by name, so the seam is in `pqtest`.
    from pqtools import pqtest as _pqtest

    def fail(*args, **kwargs):
        raise _ProcessReadError

    monkeypatch.setattr(_pqtest, "_run_process_bounded", fail)
    with pytest.raises(_pqtest.AdapterError, match="could not be read in full"):
        _pqtest._run_bounded(["irrelevant"], 10)


def test_node_bridge_names_a_read_failure_rather_than_blaming_the_json(monkeypatch):
    # Without the guard this surfaced as "invalid JSON": a local read fault
    # reported as the child's misbehaviour. `_require_node` maps the same
    # failure to its own message and runs first, so the bridge's mapping is
    # driven directly.
    def fail(*args, **kwargs):
        raise _ProcessReadError

    monkeypatch.setattr(core, "_require_node", lambda binary: None)
    monkeypatch.setattr(core, "_run_process_bounded", fail)
    with pytest.raises(NodeError, match="could not be read in full"):
        parse(SOURCE)


def test_node_version_check_refuses_a_truncated_read(monkeypatch):
    # `_require_node` would reject a prefix by luck, because its version
    # regex is a fullmatch. This pins the mapping instead of the luck.
    def fail(*args, **kwargs):
        raise _ProcessReadError

    monkeypatch.setattr(core, "_run_process_bounded", fail)
    core._require_node.cache_clear()
    try:
        with pytest.raises(NodeError, match="could not be read in full"):
            core._require_node("/nonexistent/node")
    finally:
        core._require_node.cache_clear()


def test_timeout_teardown_never_closes_a_descriptor_by_number() -> None:
    """The abandon path closes streams through their objects, never by int.

    `os.close(stream.fileno())` unblocked a reader a grandchild had pinned,
    but it bypassed the BufferedReader: its `closed` flag stayed False, and
    when the abandoned thread finally died the object's finaliser closed the
    same integer a second time - by then recycled by the OS to a later,
    unrelated call's pipe. That is how a corpus parse two test files later
    got `BRIDGE_FAILURE` from a child whose stdin vanished mid-document.

    All three pipes, stdin included. A previous cut left stdin to the writer
    thread on the reasoning that the killed child's EPIPE would free it -
    true only when the child is the last holder of the read end, which a
    grandchild breaks: one leaked descriptor and a writer stuck in
    `write()` for as long as the grandchild lived, per timed-out call.

    Asserted against the source because the recycling itself is a race.
    The runtime halves of the control are the next two tests.
    """
    # Parsed, not grepped: the comment explaining the defect quotes the very
    # call this forbids, and a text search would fail on its own explanation.
    tree = ast.parse(textwrap.dedent(inspect.getsource(_run_process_bounded)))
    calls = [
        ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
    ]
    assert "os.close" not in calls, calls
    assert not any(call.endswith(".fileno") for call in calls), calls
    # The streams the abandon path closes: the two readers, never stdin.
    readers = [
        ast.unparse(node.elt) + " <- " + ast.unparse(node.generators[0].iter)
        for node in ast.walk(tree)
        if isinstance(node, ast.ListComp)
        and node.generators
        and "stream is not None" in ast.unparse(node.generators[0])
    ]
    assert len(readers) == 1, readers
    assert "process.stdout" in readers[0] and "process.stderr" in readers[0]
    assert "process.stdin" in readers[0]


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only: relies on closing the raw pipe unblocking the reader",
)
def test_abandoned_reader_stream_is_marked_closed_so_its_finaliser_is_inert(
    monkeypatch,
):
    """The runtime half: after a timeout that abandons a reader, the real
    BufferedReader it holds must already know it is closed.

    A `BufferedReader.closed` that is still False here means the parent
    closed the integer behind its back, and the object will close that
    integer again on finalisation - whatever it belongs to by then. With the
    fix the object is closed through its raw FileIO, so `closed` is True and
    the finaliser has nothing to do.
    """
    captured: list[Any] = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        captured.append((process.stdout, process.stderr))
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    command = [
        sys.executable,
        "-c",
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "sys.stdout.write('parent done')",
    ]
    with pytest.raises(subprocess.TimeoutExpired):
        _run_process_bounded(command, None, 2)
    assert captured, "the child should have been spawned"
    stdout, stderr = captured[0]
    assert stdout.closed, "abandoned stdout reader still thinks it is open"
    assert stderr.closed, "abandoned stderr reader still thinks it is open"


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only: relies on closing the raw pipe unblocking the writer",
)
def test_abandoned_writer_behind_a_grandchild_is_freed_and_leaks_nothing():
    """The HIGH from the round-9 review, reproduced before it was fixed.

    The child exits at once but a grandchild inherits its stdin and sleeps,
    so the read end stays open and the writer's 10 MiB never drains. With
    stdin left out of the teardown that writer sat in `write()` until the
    grandchild died and one descriptor stayed open; closing stdin through
    its raw FileIO frees both.
    """
    before_threads = {t.name for t in threading.enumerate()}
    before_fds = len(os.listdir("/dev/fd"))
    command = [
        sys.executable,
        "-c",
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "sys.exit(0)",
    ]
    with pytest.raises(subprocess.TimeoutExpired):
        _run_process_bounded(command, b"x" * MAX_BYTES, 1)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        stuck = [t for t in threading.enumerate() if t.name not in before_threads]
        if not stuck:
            break
        time.sleep(0.05)
    assert not stuck, [t.name for t in stuck]
    assert len(os.listdir("/dev/fd")) <= before_fds


def test_a_failing_child_keeps_its_own_diagnosis_over_a_short_read(failing_stdout):
    # Same guard as the write side: the exit code is the better explanation,
    # and callers check it first. Without the guard `_ProcessReadError`
    # replaced "exit 3" with an account of our own pipe.
    command = [sys.executable, "-c", "raise SystemExit(3)"]
    result = _run_process_bounded(command, None, 10)
    assert result.returncode == 3


class _WriteFailsEBADF:
    """A stdin wrapper whose write fails the way a closed-under-us fd does."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        raise OSError(9, "Bad file descriptor")

    def close(self):
        return self._stream.close()

    def fileno(self):
        return self._stream.fileno()


def test_ebadf_on_write_is_recorded_not_lost_to_the_thread(monkeypatch):
    """EBADF is an OSError but not a BrokenPipeError.

    Catching only `BrokenPipeError` let it escape to threading's excepthook,
    where nothing recorded it, so the call returned a clean run of a child
    that had read nothing - which is exactly what a recycled descriptor
    produces.
    """
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        process.stdin = _WriteFailsEBADF(process.stdin)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    command = [sys.executable, "-c", "pass"]
    with pytest.raises(_ProcessWriteError):
        _run_process_bounded(command, b"payload", 10)


def test_short_write_is_raised_not_returned_as_a_delivered_payload():
    """A child that exits without reading leaves stdin half-written.

    No monkeypatching: the child simply exits before the 4 MiB payload can
    be delivered, so the write really does hit a closed pipe. Before this
    was recorded, the call returned a completed process with returncode 0
    and the child had only part of its input - the caller could not tell
    that from a full delivery.
    """
    command = [sys.executable, "-c", "pass"]
    with pytest.raises(_ProcessWriteError):
        _run_process_bounded(command, b"x" * (4 * 1024 * 1024), 10)


def test_a_failing_child_keeps_its_own_diagnosis_over_the_broken_pipe():
    """The guard on the raise: a non-zero exit is the better explanation.

    The same broken pipe happens here, but it is a consequence of the child
    exiting, not the cause. Raising on it would replace the child's exit
    code with our account of the pipe.
    """
    command = [sys.executable, "-c", "raise SystemExit(3)"]
    result = _run_process_bounded(command, b"x" * (4 * 1024 * 1024), 10)
    assert result.returncode == 3


def test_bridge_names_a_short_write_rather_than_blaming_the_child(monkeypatch):
    # Undelivered input made the bridge report the child's complaint about a
    # half-read JSON document - a generic BRIDGE_FAILURE - as the child's
    # own fault.
    def fail(*args, **kwargs):
        raise _ProcessWriteError

    monkeypatch.setattr(core, "_require_node", lambda binary: None)
    monkeypatch.setattr(core, "_run_process_bounded", fail)
    with pytest.raises(NodeError, match="input could not be written in full"):
        parse(SOURCE)


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


def test_snapshot_reads_bytes_that_text_mode_would_corrupt(tmp_path: Path):
    """Windows os.open defaults to TEXT mode: it eats \\r and stops at 0x1A.

    Any binary container (.pbix, .xlsx) can contain both. Without O_BINARY the
    read silently truncates, which surfaced as an intermittent CI failure because
    zip bytes vary with the embedded timestamp.
    """
    path = tmp_path / "binary.pq"
    payload = b"let A = 1 in A\r\n\x1aTRAILING BYTES AFTER THE EOF MARKER\r\n"
    path.write_bytes(payload)
    assert core._snapshot(path).data == payload
