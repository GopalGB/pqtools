"""Offline, deliberately narrow Power Query M source operations."""

from __future__ import annotations

import contextlib
import difflib
import enum
import errno
import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_BYTES = 10 * 1024 * 1024
NODE_TIMEOUT_SECONDS = 30
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREDENTIAL_NAME = re.compile(r"(?i)^(password|token|secret)$")
_FILE_SUFFIXES = (".pq", ".m", ".pqm")
_RESERVED = {
    "and",
    "as",
    "each",
    "else",
    "error",
    "false",
    "if",
    "in",
    "is",
    "let",
    "meta",
    "not",
    "null",
    "or",
    "otherwise",
    "section",
    "shared",
    "then",
    "true",
    "try",
    "type",
}


class MQueryError(Exception):
    code = "MQUERY_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NodeError(MQueryError):
    code = "NODE_ERROR"


class ParseError(MQueryError):
    code = "M_PARSE_ERROR"


class RenameRefusal(MQueryError):
    code = "M_RENAME_REFUSED"


class SafeWriteError(MQueryError):
    code = "M_SAFE_WRITE_REFUSED"


class AdapterError(MQueryError):
    code = "M_ADAPTER_ERROR"


class _ProcessOutputLimit(Exception):
    pass


class _ProcessWriteError(Exception):
    """The child's stdin closed before the whole payload was delivered.

    Same shape as `_ProcessReadError`, opposite direction. A short write left
    the child parsing a truncated payload, so whatever it then said about
    that payload was reported as the child's own misbehaviour - the bridge's
    generic `BRIDGE_FAILURE` for a JSON document cut in half.
    """


class _ProcessReadError(Exception):
    """A reader thread died before the child's output was fully drained.

    The buffer it filled is a prefix of the real output, and nothing about
    it says so. Callers that parse the output structurally (JSON, a version
    regex) would reject the truncation by luck; `pqtest._run_bounded` returns
    it verbatim and would not. So the read failure is raised rather than
    swallowed, and every caller maps it to its own typed error.
    """


@dataclass(frozen=True)
class Diagnostic:
    file: str = "<string>"
    line: int = 1
    column: int = 1
    code: str = "M000"
    severity: str = "error"
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class FileSnapshot:
    data: bytes
    mode: int
    device: int
    inode: int
    size: int
    mtime_ns: int


def _node_binary() -> str:
    configured = os.environ.get("MQUERY_NODE")
    if configured:
        return configured
    if os.name == "nt":
        found = shutil.which("node", path=os.environ.get("PATH", ""))
    else:
        found = shutil.which("node")
    if found is None:
        raise NodeError("Node.js 22 or newer is required")
    if Path(found).resolve().parent == Path.cwd().resolve():
        raise NodeError("refusing to run node from the current directory")
    return found


def _newline(source: str) -> str:
    return "\r\n" if "\r\n" in source else "\n"


def _run_process_bounded(
    command: list[str], input_data: bytes | None, timeout: int
) -> subprocess.CompletedProcess[bytes]:
    """Run `command`, bounded by `timeout` and `MAX_BYTES`, and never lie.

    A pipe fault raises rather than returning a short buffer - that is the
    point of the two sentinels - but **only when the child exited 0**. When
    `returncode != 0` the child's own account wins, so the returned
    `CompletedProcess` may carry a truncated `stdout`/`stderr` with nothing
    to mark it as truncated. Every caller today rejects a non-zero return
    before reading the buffers; a caller that wants to log stderr from a
    failed child must know that the tail may be missing.
    """
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        buffers = [bytearray(), bytearray()]
        exceeded = threading.Event()
        read_failure: list[BaseException] = []
        write_failure: list[BaseException] = []

        def read(stream: Any, buffer: bytearray) -> None:
            try:
                while chunk := stream.read(65536):
                    if len(buffer) + len(chunk) > MAX_BYTES:
                        exceeded.set()
                        process.kill()
                        return
                    buffer.extend(chunk)
            except (OSError, ValueError) as error:
                # `list.append` is atomic, so no lock is needed for the two
                # reader threads. Returning here without recording would hand
                # back a truncated buffer that looks exactly like complete
                # output.
                read_failure.append(error)

        threads = [
            threading.Thread(target=read, args=(stream, buffer), daemon=True)
            for stream, buffer in zip(
                (process.stdout, process.stderr), buffers, strict=True
            )
        ]
        if process.stdin is not None:
            stdin = process.stdin
            input_payload = input_data or b""

            def write() -> None:
                try:
                    stdin.write(input_payload)
                except OSError as error:
                    # EPIPE: the child closed stdin early and has only part
                    # of the payload. EBADF: the descriptor was closed under
                    # this thread. Either way the child did not get the
                    # document, and `BrokenPipeError` alone let EBADF escape
                    # to threading's excepthook unrecorded, so the call
                    # returned a clean run of a child that had read nothing.
                    write_failure.append(error)
                finally:
                    # NOT recorded, deliberately. When the timeout path below
                    # abandoned this thread it already closed the same stream
                    # through its raw FileIO, so this close is a no-op that
                    # raises: EBADF as `OSError`, or `ValueError` from the
                    # buffered layer refusing to flush an already-closed raw.
                    # Either says the parent tore the pipe down, not that the
                    # child got a short payload - and recording it failed a
                    # legitimate parse in the full suite. The genuine short
                    # write is caught above, where it is unambiguous.
                    with contextlib.suppress(OSError, ValueError):
                        stdin.close()

            threads.append(threading.Thread(target=write, daemon=True))
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            for thread in threads:
                thread.join(max(0.0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread in threads):
                if process.poll() is None:
                    process.kill()
                # Close all three pipes through their raw FileIO, never by
                # integer. `os.close(stream.fileno())` unblocked a thread
                # stuck on a pipe a grandchild kept open, but it bypassed the
                # buffered object, whose `closed` flag stayed False; when the
                # abandoned thread finally died, the object's finaliser closed
                # the same number a second time - by then recycled by the OS
                # to a later, unrelated call's pipe, whose child lost its
                # stdin mid-payload and reported the truncated document as
                # its own error. `raw.close()` has the same unblocking effect,
                # does not take the buffer lock the stuck call holds (so it
                # does not hang), and marks the object closed so both the
                # finaliser and the writer's own `finally: close()` are no-ops.
                #
                # stdin is included. A previous cut left it to the writer on
                # the reasoning that the killed child's EPIPE would free it -
                # true only when the child is the LAST holder of the read end,
                # which is exactly what a grandchild breaks: measured, one
                # leaked descriptor and a writer blocked in `write()` for as
                # long as the grandchild lived, per timed-out call.
                streams = [
                    stream
                    for stream in (process.stdout, process.stderr, process.stdin)
                    if stream is not None
                ]
                # Detach first so the context manager's close() below cannot
                # block on a stream an abandoned thread still holds.
                process.stdout = process.stderr = process.stdin = None
                for stream in streams:
                    with contextlib.suppress(OSError, ValueError):
                        getattr(stream, "raw", stream).close()
                raise subprocess.TimeoutExpired(command, timeout)
        if exceeded.is_set():
            raise _ProcessOutputLimit
        # Both only when the child itself succeeded. If it exited non-zero it
        # has its own account of what went wrong - a broken pipe is usually a
        # consequence of that exit, and a short read of its output still
        # leaves the exit code - so raising here would replace the child's
        # diagnosis with ours. Callers check `returncode` first.
        if process.returncode == 0:
            if read_failure:
                raise _ProcessReadError from read_failure[0]
            if write_failure:
                raise _ProcessWriteError from write_failure[0]
        return subprocess.CompletedProcess(
            command, process.returncode, *map(bytes, buffers)
        )


@lru_cache(maxsize=4)
def _require_node(binary: str) -> None:
    try:
        result = _run_process_bounded([binary, "--version"], None, 5)
    except _ProcessReadError as error:
        # A local pipe fault, not a missing Node: the same mis-blame `_bridge`
        # stopped making, and llms.txt tells the reader to retry once. There
        # is no `_ProcessWriteError` clause because this call passes no input,
        # so there is no writer thread that could record one - the same reason
        # `pqtest._run_bounded` has none.
        raise NodeError(
            "Node version check: process output could not be read in full"
        ) from error
    except (OSError, subprocess.SubprocessError, _ProcessOutputLimit) as error:
        raise NodeError("Node.js 22 or newer is required") from error
    if result.returncode or not re.fullmatch(
        rb"v(2[2-9]|[3-9]\d|\d{3,})\.\d+\.\d+\S*\s*", result.stdout
    ):
        raise NodeError("Node.js 22 or newer is required")


def _bridge(source: str, kind: str, **options: str) -> dict[str, Any]:
    stripped = source[1:] if source.startswith("\ufeff") else source
    try:
        payload = json.dumps(
            {"source": stripped, "kind": kind, "newline": _newline(source), **options}
        ).encode("utf-8", "strict")
        source_size = len(source.encode("utf-8", "strict"))
    except UnicodeEncodeError as error:
        raise MQueryError("source must be valid UTF-8") from error
    if source_size > MAX_BYTES or len(payload) > MAX_BYTES:
        raise MQueryError("input exceeds 10 MiB")
    bridge = Path(__file__).with_name("_bridge.cjs")
    node = _node_binary()
    _require_node(node)
    try:
        result = _run_process_bounded(
            [node, str(bridge)], payload, NODE_TIMEOUT_SECONDS
        )
    except FileNotFoundError as error:
        raise NodeError("Node.js 22 or newer is required") from error
    except subprocess.TimeoutExpired as error:
        raise NodeError("Node subprocess timed out after 30 seconds") from error
    except _ProcessOutputLimit as error:
        raise NodeError("Node output exceeds 10 MiB") from error
    except _ProcessReadError as error:
        raise NodeError("Node bridge output could not be read in full") from error
    except _ProcessWriteError as error:
        raise NodeError("Node bridge input could not be written in full") from error
    if result.returncode:
        raise NodeError(f"Node bridge failed with exit {result.returncode}")
    try:
        response: dict[str, Any] = json.loads(result.stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NodeError("Node bridge returned invalid JSON") from error
    if response.get("error") == "PARSE_ERROR":
        raise ParseError(
            f"parse error at {response.get('line', 1)}:{response.get('column', 1)}: "
            f"{response.get('message', 'invalid Power Query source')}"
        )
    if response.get("error"):
        raise NodeError(str(response["error"]))
    return response


# M's character escapes. `""` is handled alongside them below (it is a
# doubled delimiter, not a `#(...)` sequence).
_TEXT_ESCAPES = {"cr": "\r", "lf": "\n", "tab": "\t", "#": "#"}


def _decode_escape(inner: str, token: str) -> str:
    """Decode the body of one ``#(...)`` sequence.

    Comma-separated, so `#(cr,lf)` is one sequence producing two characters -
    which is how every M query that wants a Windows line ending writes it.
    """
    if not inner:
        raise ParseError(f"empty escape sequence #() in {token}")
    out = []
    for item in inner.split(","):
        if item in _TEXT_ESCAPES:
            out.append(_TEXT_ESCAPES[item])
            continue
        if len(item) in (4, 8) and all(c in "0123456789abcdefABCDEF" for c in item):
            code = int(item, 16)
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                raise ParseError(
                    f"escape #({item}) is not a Unicode code point, in {token}"
                )
            out.append(chr(code))
            continue
        raise ParseError(
            f"unknown escape #({item}) in {token}: expected cr, lf, tab, #, "
            "or 4 or 8 hex digits"
        )
    return "".join(out)


def decode_escapes(body: str, token: str) -> str:
    """Decode the BODY of a text literal or quoted identifier.

    M gives both forms the same contents: the grammar spells a
    quoted-identifier as ``#"`` followed by text-literal characters, which
    include the ``#(...)`` escape sequences. One decoder for both, because
    there were two and only one of them decoded anything - see
    `unquote_identifier` for what that cost.
    """
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == '"' and body[index + 1 : index + 2] == '"':
            out.append('"')
            index += 2
        elif char == "#" and body[index + 1 : index + 2] == "(":
            close = body.find(")", index + 2)
            if close == -1:
                raise ParseError(f"unterminated escape sequence in {token}")
            out.append(_decode_escape(body[index + 2 : close], token))
            index = close + 1
        else:
            # A bare `#` is an ordinary character in M unless `(` follows it.
            out.append(char)
            index += 1
    return "".join(out)


def unquote_identifier(text: str) -> str:
    """``#"First Name"`` -> ``First Name``; anything else unchanged.

    M spells an identifier that is not a bare name as ``#"..."``. The ``#"``
    and ``"`` are syntax, not part of the name, and what sits between them is
    text-literal content - so ``""`` is an embedded quote AND ``#(tab)`` is a
    tab, exactly as in a string.

    That second half was missing, and Microsoft's own Table.TransformColumnNames
    Example 1 is the proof: it names a column ``#"Col#(tab)umn"`` and expects
    ``Text.Clean`` to yield ``Column``. Text.Clean strips control characters,
    so it can only produce ``Column`` if the identifier really does contain a
    tab. pqtools read the name as the twelve literal characters
    ``Col#(tab)umn``, which Text.Clean then left alone.

    This lives in core because two independent code paths read identifiers -
    the evaluator (`evaluate._identifier_text`) and the section splitter
    (`containers.split_shared`) - and both were wrong in the same way. Fixing
    them separately would have let them drift apart again; the second one only
    surfaced because a query added under a quoted name could not then be run
    by that name.
    """
    if len(text) >= 3 and text.startswith('#"') and text.endswith('"'):
        body = text[2:-1]
        try:
            return decode_escapes(body, text)
        except ParseError:
            # A name is not data. `pq list` on a real workbook must not refuse
            # the whole file because one query name holds a `#(` that is not a
            # valid escape; keeping the raw name lists it and lets the user
            # act. Text literals take the strict path - see
            # evaluate._parse_text_literal - because there a bad escape is a
            # bad VALUE, and passing it through would corrupt the result.
            return body.replace('""', '"')
    return text


def parse(source: str) -> dict[str, Any]:
    """Return a stable, JSON-safe view from Microsoft's pinned parser."""
    return _bridge(source, "parse")


def ast(source: str) -> dict[str, Any]:
    """Return the parsed AST, pruned to kind/value/children/line/column.

    Used by :mod:`pqtools.evaluate` to walk and evaluate a query offline.
    """
    return dict(_bridge(source, "ast")["ast"])


def _preserve_layout(updated: str, original: str) -> str:
    if _newline(original) == "\r\n":
        updated = updated.replace("\r\n", "\n").replace("\n", "\r\n")
    else:
        updated = updated.replace("\r\n", "\n")
    if original.startswith("\ufeff"):
        if not updated.startswith("\ufeff"):
            updated = "\ufeff" + updated
    elif updated.startswith("\ufeff"):
        updated = updated[1:]
    if not original.endswith(("\n", "\r")):
        return updated.rstrip("\r\n")
    if not updated.endswith(("\n", "\r")):
        updated += _newline(original)
    return updated


def format_source(source: str) -> str:
    return _preserve_layout(str(_bridge(source, "format")["formatted"]), source)


def dependencies(source: str) -> list[str]:
    return _dependencies_from(parse(source))


def _dependencies_from(parsed: dict[str, Any]) -> list[str]:
    tokens = parsed["tokens"]
    bound = {
        str(binding["name"])
        for binding in (parsed.get("analysis") or {}).get("bindings", [])
    }
    return sorted(
        {
            str(item["text"])
            for index, item in enumerate(tokens[:-1])
            if item["kind"] == "Identifier"
            and tokens[index + 1]["kind"] == "LeftParenthesis"
            and str(item["text"]) not in bound
        }
    )


def _rename_plan(source: str, old: str) -> dict[str, Any]:
    if '#"' in source or "[" in source or "=>" in source or not source.isascii():
        raise RenameRefusal(
            "quoted, record, lambda, or non-ASCII rename is unsupported"
        )
    try:
        return _bridge(source, "rename", old=old)
    except NodeError as error:
        raise RenameRefusal(
            "rename supports one unquoted top-level let scope"
        ) from error


def rename(source: str, old: str, new: str) -> str:
    """Rename one unquoted top-level binding and its unambiguous references."""
    has_bom = source.startswith("\ufeff")
    if has_bom:
        source = source[1:]
    if not _IDENTIFIER.fullmatch(old) or not _IDENTIFIER.fullmatch(new) or old == new:
        raise RenameRefusal("rename requires distinct unquoted identifiers")
    if new.lower() in _RESERVED:
        raise RenameRefusal("rename target is a reserved M keyword")
    plan = _rename_plan(source, old)
    declarations = list(plan["bindings"])
    if new in declarations:
        raise RenameRefusal("rename target collides with an existing let binding")
    if any(
        token["kind"] == "Identifier" and str(token["text"]) == new
        for token in plan["tokens"]
    ):
        raise RenameRefusal("rename target already appears in the source")
    edits = [(int(start), int(end)) for start, end in plan["spans"]]
    if not edits:
        raise RenameRefusal("target must name exactly one top-level let binding")
    edits = sorted(edits)
    for index in range(len(edits) - 1):
        if edits[index][1] > edits[index + 1][0]:
            raise RenameRefusal("rename spans overlap")
    for start, end in reversed(edits):
        source = source[:start] + new + source[end:]
    if has_bom:
        source = "\ufeff" + source
    parse(source)
    return source


def replace_source(source: str, replacement: str) -> str:
    """Replace complete source only - never an unsafe partial-text match."""
    try:
        size = len(replacement.encode("utf-8", "strict"))
    except UnicodeEncodeError as error:
        raise MQueryError("source must be valid UTF-8") from error
    if size > MAX_BYTES:
        raise MQueryError("replacement exceeds 10 MiB")
    parse(replacement)
    return replacement


def check(source: str, file: str = "<string>") -> list[Diagnostic]:
    try:
        parsed = parse(source)
    except ParseError as error:
        match = re.search(r"(\d+):(\d+)", error.message)
        line, column = (int(item) for item in match.groups()) if match else (1, 1)
        return [Diagnostic(file, line, column, error.code, "error", error.message)]
    diagnostics: list[Diagnostic] = []
    analysis = parsed.get("analysis") or {}
    bindings = analysis.get("bindings", [])
    names = [str(binding["name"]) for binding in bindings]
    counts = Counter(names)
    for binding in bindings:
        if counts[str(binding["name"])] > 1:
            diagnostics.append(
                Diagnostic(
                    file,
                    int(binding["line"]),
                    int(binding["column"]),
                    "M001",
                    "error",
                    f"duplicate let binding: {binding['name']}",
                )
            )

    by_name = {str(binding["name"]): binding for binding in bindings}
    result_references = list(analysis.get("resultReferences", []))
    reachable = {
        str(reference["name"])
        for reference in result_references
        if str(reference["name"]) in by_name
    }
    pending = list(reachable)
    while pending:
        binding = by_name[pending.pop()]
        for reference in binding.get("references", []):
            name = str(reference["name"])
            if name in by_name and name not in reachable:
                reachable.add(name)
                pending.append(name)
    for binding in bindings:
        if str(binding["name"]) not in reachable:
            diagnostics.append(
                Diagnostic(
                    file,
                    int(binding["line"]),
                    int(binding["column"]),
                    "M004",
                    "warning",
                    f"unreachable let binding: {binding['name']}",
                )
            )

    references = result_references + [
        reference for binding in bindings for reference in binding.get("references", [])
    ]
    for reference in references:
        name = str(reference["name"])
        if name not in by_name and "." not in name:
            diagnostics.append(
                Diagnostic(
                    file,
                    int(reference["line"]),
                    int(reference["column"]),
                    "M005",
                    "warning",
                    f"unresolved unqualified reference: {name}",
                )
            )

    tokens = parsed["tokens"]
    for index, token in enumerate(tokens):
        if token["kind"] != "Identifier":
            continue
        text = str(token["text"])
        if (
            text == "Web.Contents"
            and index + 1 < len(tokens)
            and tokens[index + 1]["kind"] == "LeftParenthesis"
            and not (
                index + 2 < len(tokens) and tokens[index + 2]["kind"] == "TextLiteral"
            )
        ):
            diagnostics.append(
                Diagnostic(
                    file,
                    int(token["line"]),
                    int(token["column"]),
                    "M002",
                    "warning",
                    "dynamic Web.Contents URL",
                )
            )
        if (
            _CREDENTIAL_NAME.match(text)
            and index + 2 < len(tokens)
            and tokens[index + 1]["kind"] == "Equal"
            and tokens[index + 2]["kind"] == "TextLiteral"
        ):
            diagnostics.append(
                Diagnostic(
                    file,
                    int(token["line"]),
                    int(token["column"]),
                    "M003",
                    "warning",
                    "credential-like literal",
                )
            )
    for dependency in _dependencies_from(parsed):
        if dependency.endswith(".Contents"):
            diagnostics.append(
                Diagnostic(file, 1, 1, "M006", "info", f"source function: {dependency}")
            )
    return diagnostics


# ---------------------------------------------------------------------------
# The filesystem errno taxonomy - one table, one decision
# ---------------------------------------------------------------------------
#
# Every filesystem call on the write path can fail in two ways that must never
# be confused:
#
#   * the path does not resolve, or the OS refused the operation - an I/O
#     FACT. The OSError propagates untouched and the CLI renders it as
#     M_IO_ERROR carrying the OS's own reason.
#   * the file resolves but is unsafe to WRITE - a symlink, a non-regular
#     file, more than one hard link - a REFUSAL. SafeWriteError, which the
#     CLI renders as M_SAFE_WRITE_REFUSED.
#
# This is not cosmetic. `M_SAFE_WRITE_REFUSED: writes require a regular,
# non-symlink, single-link file` printed for `pq check missing.pq` describes
# something the user did not do, and it goes into the --json that consumers
# parse.
#
# It was got wrong in four consecutive review rounds because the decision was
# made inline at each call site, with fresh reasoning every time:
#
#   r12  wrapped EVERY failed open as a write refusal, so a missing file read
#        as one.
#   r13  fixed that, then wrapped ELOOP across a whole try block - which
#        caught os.lstat's ELOOP, i.e. a symlink loop in a DIRECTORY
#        component: an ordinary broken path, reported as a write refusal.
#   r14  scoped that to os.open, and left os.open on the LOCK file reporting
#        ENOTDIR as "unable to acquire safe source lock" - a contended lock
#        that was never contended.
#   now  the decision is made once, in the table below.
#
# The key is (call site, errno) and NOT errno alone. That is the trap all
# three previous fixes fell into: ELOOP means "a directory component is a
# symlink loop" when os.lstat raises it, and "the final component is a
# symlink that O_NOFOLLOW refused to follow" when os.open does. Same number,
# opposite verdicts. An errno-only table cannot express that and would have
# re-created the r13 bug by construction.


class _FsCall(enum.Enum):
    """Which call raised - the half of the key that errno alone cannot carry."""

    RESOLVE = "os.lstat"
    OPEN_SOURCE = "os.open with O_NOFOLLOW, on the source file"
    OPEN_LOCK = "os.open with O_CREAT|O_NOFOLLOW, on the lock file"
    CREATE_TEMP = "tempfile.mkstemp, in the source's directory"


UNSAFE_TO_WRITE = "writes require a regular, non-symlink, single-link file"
LOCK_UNSAFE = "source lock must be a regular single-link file"

# (call, errno) -> the refusal message. ANYTHING NOT LISTED IS AN I/O FACT and
# propagates untouched. The table is deliberately tiny and closed: adding an
# entry means claiming that failure is about write-safety rather than about
# the path, and that claim needs a reason written next to it.
_WRITE_SAFETY: dict[tuple[_FsCall, int], str] = {
    # O_NOFOLLOW refused the final component. os.lstat has already returned
    # and said it was not a symlink, so one appeared between the two calls -
    # exactly the TOCTOU race the flag exists to close.
    (_FsCall.OPEN_SOURCE, errno.ELOOP): UNSAFE_TO_WRITE,
    # The same, for the lock file: a symlinked lock is not one we will follow.
    (_FsCall.OPEN_LOCK, errno.ELOOP): LOCK_UNSAFE,
}

# _FsCall.RESOLVE and _FsCall.CREATE_TEMP appear in NO entry, and that is the
# point rather than an omission:
#   RESOLVE      - os.lstat cannot report a write-safety problem. It either
#                  resolves the path or it does not, and every way it fails
#                  (ENOENT, ENOTDIR, ELOOP on a directory component, EACCES)
#                  is a fact about the path. This is the r13 bug, expressed
#                  as an empty row that cannot be filled in by accident.
#   CREATE_TEMP  - mkstemp fails when the DIRECTORY will not take a new file.
#                  "Permission denied: /dir/.q.pq.ab12.tmp" is the true
#                  reason; "unable to create temporary file" restated the
#                  call without adding anything the OS had not said.
# Lock CONTENTION is not here either, because os.open does not report it:
# it surfaces from _lock_file (flock/msvcrt), which keeps its own message.


def _final_component_is_a_symlink(path: Path) -> bool:
    """Is `path` itself a symlink, as opposed to sitting under a broken one?

    `os.lstat` does not follow the final component, so it answers exactly
    that question and nothing else. A failure here means the path does not
    resolve at all, which is not a symlink - False is the right answer, and
    the caller then propagates the original OSError.
    """
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except OSError:
        return False


def _write_refusal(error: OSError, call: _FsCall, path: Path) -> str | None:
    """The refusal message if this failure is about write-safety, else None.

    None means "let the OSError through untouched". Callers are uniform:

        except OSError as error:
            refusal = _write_refusal(error, _FsCall.OPEN_SOURCE, path)
            if refusal is not None:
                raise SafeWriteError(refusal) from error
            raise

    ELOOP is asked about rather than assumed. `os.open` raises it BOTH when
    O_NOFOLLOW refuses a symlinked final component (a write-safety refusal)
    and when any DIRECTORY component of the path is a symlink loop (an
    ordinary broken path). Round 13 conflated those at the source file, and
    building the behaviour matrix for this rewrite caught the identical
    conflation still live at the lock file, where - unlike the source file -
    no preceding `lstat` had already ruled the directory case out. Deciding
    it from the errno alone is not possible, so we ask the filesystem which
    of the two it is instead of reasoning about which one "must" have
    happened.
    """
    message = _WRITE_SAFETY.get((call, error.errno or 0))
    if message is None:
        return None
    if error.errno == errno.ELOOP and not _final_component_is_a_symlink(path):
        return None
    return message


def _snapshot(path: Path) -> FileSnapshot:
    # O_BINARY is REQUIRED on Windows: os.open there defaults to TEXT mode, which
    # translates \r\n and treats 0x1A as end-of-file. Reading a .pbix or .xlsx that
    # way silently truncates or mangles it. Whether that corrupts a given file
    # depends on its bytes, so it presented as an intermittent CI failure rather
    # than a consistent one. It is a no-op flag everywhere else.
    # O_NONBLOCK keeps a FIFO/device open() from blocking forever waiting for
    # a writer; it has no effect on regular files. The S_ISREG check below
    # then rejects the non-regular file immediately instead of hanging.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise SafeWriteError(UNSAFE_TO_WRITE)
    except OSError as error:
        refusal = _write_refusal(error, _FsCall.RESOLVE, path)
        if refusal is not None:  # pragma: no cover - RESOLVE has no entries
            raise SafeWriteError(refusal) from error
        raise
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        # This function is the READ path too (`cli._source`, `export`,
        # `containers`), so a file that will not open is missing or
        # unreadable - not a file that is unsafe to write. The table decides;
        # see the taxonomy above it for why the call site is half the key.
        refusal = _write_refusal(error, _FsCall.OPEN_SOURCE, path)
        if refusal is not None:
            raise SafeWriteError(refusal) from error
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SafeWriteError(UNSAFE_TO_WRITE)
        if info.st_size > MAX_BYTES:
            raise SafeWriteError("input exceeds 10 MiB")
        data = bytearray()
        while True:
            chunk = os.read(descriptor, min(65536, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise SafeWriteError("input exceeds 10 MiB")
    finally:
        os.close(descriptor)
    return FileSnapshot(
        bytes(data),
        stat.S_IMODE(info.st_mode),
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
    )


def _diff(path: Path, original: bytes, updated: str) -> str:
    try:
        original_text = original.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise SafeWriteError("source must be valid UTF-8") from error
    return "".join(
        difflib.unified_diff(
            original_text.splitlines(True),
            updated.splitlines(True),
            fromfile=str(path),
            tofile=str(path),
        )
    )


def _lock_file(descriptor: int) -> None:
    if os.name == "nt":
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        locker = importlib.import_module("msvcrt")
        locker.locking(descriptor, locker.LK_LOCK, 1)
    else:
        locker = importlib.import_module("fcntl")
        locker.flock(descriptor, locker.LOCK_EX)


def _unlock_file(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        locker = importlib.import_module("msvcrt")
        locker.locking(descriptor, locker.LK_UNLCK, 1)
    else:
        locker = importlib.import_module("fcntl")
        locker.flock(descriptor, locker.LOCK_UN)


def _atomic_write(
    path: Path, build: Callable[[FileSnapshot], tuple[str, bytes | None]]
) -> str:
    """Lock, snapshot, and atomically replace path's bytes.

    Acquires path's cross-process advisory lock, snapshots it, calls
    ``build(snapshot)`` to get ``(diff, updated)``, and - only if `updated`
    is not None and differs from the snapshot - writes it to a sibling temp
    file, fsyncs, chmods to match, re-checks the snapshot once more, then
    ``os.replace``s it into place and fsyncs the parent directory. Returns
    `diff` either way. This is the one atomic-replace implementation in the
    package: `update_file`'s ``--write`` path and `containers.write_sections`
    both call it, so there is never a second hand-rolled write path.
    """
    lock = path.with_name(f".{path.name}.lock")
    lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    temporary: Path | None = None
    try:
        lock_fd = os.open(lock, lock_flags, 0o600)
    except OSError as error:
        # Opening the lock file cannot report CONTENTION - flock does, below.
        # So the only refusal here is a symlinked lock; everything else is a
        # fact about the directory and reads better in the OS's own words.
        refusal = _write_refusal(error, _FsCall.OPEN_LOCK, lock)
        if refusal is not None:
            raise SafeWriteError(refusal) from error
        raise
    acquired = False
    try:
        lock_info = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
            raise SafeWriteError(LOCK_UNSAFE)
        try:
            _lock_file(lock_fd)
        except OSError as error:
            raise SafeWriteError("unable to acquire safe source lock") from error
        acquired = True
        snapshot = _snapshot(path)
        diff, updated = build(snapshot)
        if updated is None or updated == snapshot.data:
            return diff
        if _snapshot(path) != snapshot:
            raise SafeWriteError("source changed during operation")
        try:
            descriptor, name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
        except OSError as error:
            # No table entry: mkstemp fails when the DIRECTORY will not take
            # a new file, and the OS says why better than we can.
            refusal = _write_refusal(error, _FsCall.CREATE_TEMP, path.parent)
            if refusal is not None:  # pragma: no cover - no entries
                raise SafeWriteError(refusal) from error
            raise
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, snapshot.mode)
        if _snapshot(path) != snapshot:
            raise SafeWriteError("source changed before atomic replacement")
        os.replace(temporary, path)
        with contextlib.suppress(OSError):
            directory_fd = os.open(
                path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return diff
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()
        try:
            if acquired:
                with contextlib.suppress(OSError):
                    _unlock_file(lock_fd)
        finally:
            os.close(lock_fd)
        # Lock removal is best-effort; the snapshot re-checks above remain
        # the correctness guard against a concurrent writer.
        with contextlib.suppress(OSError):
            os.unlink(lock)


def update_file(
    path: Path, transform: Callable[[str], str], write: bool = False
) -> str:
    if path.suffix not in _FILE_SUFFIXES:
        raise SafeWriteError("unsupported source file extension")
    if not write:
        snapshot = _snapshot(path)
        try:
            original = snapshot.data.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise SafeWriteError("source must be valid UTF-8") from error
        updated = _preserve_layout(transform(original), original)
        return _diff(path, snapshot.data, updated)

    def build(snapshot: FileSnapshot) -> tuple[str, bytes | None]:
        try:
            original = snapshot.data.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise SafeWriteError("source must be valid UTF-8") from error
        updated = _preserve_layout(transform(original), original)
        diff = _diff(path, snapshot.data, updated)
        return diff, updated.encode()

    return _atomic_write(path, build)
