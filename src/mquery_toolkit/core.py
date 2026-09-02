"""Offline, deliberately narrow Power Query M source operations."""

from __future__ import annotations

import contextlib
import difflib
import importlib
import json
import os
import re
import stat
import subprocess
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_BYTES = 10 * 1024 * 1024
NODE_TIMEOUT_SECONDS = 30
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
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
    local = (
        Path(__file__).parents[2]
        / ".tools"
        / "node-v22.23.2-darwin-arm64"
        / "bin"
        / "node"
    )
    if local.is_file():
        return str(local)
    return "node"


def _newline(source: str) -> str:
    return "\r\n" if "\r\n" in source else "\n"


def _run_process_bounded(
    command: list[str], input_data: bytes | None, timeout: int
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()

    def read(stream: Any, buffer: bytearray) -> None:
        while chunk := stream.read(65536):
            if len(buffer) + len(chunk) > MAX_BYTES:
                exceeded.set()
                process.kill()
                return
            buffer.extend(chunk)

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
            except BrokenPipeError:
                pass
            finally:
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
            thread.join()
    if exceeded.is_set():
        raise _ProcessOutputLimit
    return subprocess.CompletedProcess(
        command, process.returncode, *map(bytes, buffers)
    )


@lru_cache(maxsize=4)
def _require_node(binary: str) -> None:
    try:
        result = _run_process_bounded([binary, "--version"], None, 5)
    except (OSError, subprocess.SubprocessError, _ProcessOutputLimit) as error:
        raise NodeError("Node.js 22 or newer is required") from error
    if result.returncode or not re.fullmatch(
        rb"v(2[2-9]|[3-9]\d)\.\d+\.\d+\s*", result.stdout
    ):
        raise NodeError("Node.js 22 or newer is required")


def _bridge(source: str, kind: str, **options: str) -> dict[str, Any]:
    try:
        payload = json.dumps(
            {"source": source, "kind": kind, "newline": _newline(source), **options}
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


def parse(source: str) -> dict[str, Any]:
    """Return a stable, JSON-safe view from Microsoft's pinned parser."""
    return _bridge(source, "parse")


def _preserve_layout(updated: str, original: str) -> str:
    if _newline(original) == "\r\n":
        updated = updated.replace("\r\n", "\n").replace("\n", "\r\n")
    else:
        updated = updated.replace("\r\n", "\n")
    return updated if original.endswith(("\n", "\r")) else updated.rstrip("\r\n")


def format_source(source: str) -> str:
    return _preserve_layout(str(_bridge(source, "format")["formatted"]), source)


def dependencies(source: str) -> list[str]:
    parsed = parse(source)
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
    if not _IDENTIFIER.fullmatch(old) or not _IDENTIFIER.fullmatch(new) or old == new:
        raise RenameRefusal("rename requires distinct unquoted identifiers")
    if new.lower() in _RESERVED:
        raise RenameRefusal("rename target is a reserved M keyword")
    plan = _rename_plan(source, old)
    declarations = list(plan["bindings"])
    if new in declarations:
        raise RenameRefusal("rename target collides with an existing let binding")
    edits = [(int(start), int(end)) for start, end in plan["spans"]]
    if not edits:
        raise RenameRefusal("target must name exactly one top-level let binding")
    for start, end in reversed(edits):
        source = source[:start] + new + source[end:]
    parse(source)
    return source


def replace_source(source: str, replacement: str) -> str:
    """Replace complete source only - never an unsafe partial-text match."""
    if len(replacement.encode()) > MAX_BYTES:
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

    for web_match in re.finditer(r"Web\.Contents\s*\(\s*", source):
        if source[web_match.end() : web_match.end() + 1] != '"':
            line = source.count("\n", 0, web_match.start()) + 1
            column = web_match.start() - source.rfind("\n", 0, web_match.start())
            diagnostics.append(
                Diagnostic(
                    file, line, column, "M002", "warning", "dynamic Web.Contents URL"
                )
            )
    for credential_match in re.finditer(
        r"(?i)(password|token|secret)\s*=\s*\"", source
    ):
        line = source.count("\n", 0, credential_match.start()) + 1
        column = credential_match.start() - source.rfind(
            "\n", 0, credential_match.start()
        )
        diagnostics.append(
            Diagnostic(file, line, column, "M003", "warning", "credential-like literal")
        )
    for dependency in dependencies(source):
        if dependency.endswith(".Contents"):
            diagnostics.append(
                Diagnostic(file, 1, 1, "M006", "info", f"source function: {dependency}")
            )
    return diagnostics


def _snapshot(path: Path) -> FileSnapshot:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SafeWriteError(
            "writes require a regular, non-symlink, single-link file"
        ) from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SafeWriteError(
                "writes require a regular, non-symlink, single-link file"
            )
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


def dry_diff(path: Path, updated: str) -> str:
    snapshot = _snapshot(path)
    return _diff(path, snapshot.data, updated)


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


def update_file(
    path: Path, transform: Callable[[str], str], write: bool = False
) -> str:
    if path.suffix not in _FILE_SUFFIXES and not path.name.endswith(".query.pq"):
        raise SafeWriteError("unsupported source file extension")
    if not write:
        snapshot = _snapshot(path)
        try:
            original = snapshot.data.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise SafeWriteError("source must be valid UTF-8") from error
        updated = _preserve_layout(transform(original), original)
        return _diff(path, snapshot.data, updated)
    lock = path.with_name(f".{path.name}.lock")
    lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock, lock_flags, 0o600)
    except OSError as error:
        raise SafeWriteError("unable to acquire safe source lock") from error
    try:
        lock_info = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
            raise SafeWriteError("source lock must be a regular single-link file")
        _lock_file(lock_fd)
        snapshot = _snapshot(path)
        try:
            original = snapshot.data.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise SafeWriteError("source must be valid UTF-8") from error
        updated = _preserve_layout(transform(original), original)
        diff = _diff(path, snapshot.data, updated)
        if updated == original:
            return diff
        if _snapshot(path) != snapshot:
            raise SafeWriteError("source changed during operation")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("xb") as handle:
            handle.write(updated.encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, snapshot.mode)
        if _snapshot(path) != snapshot:
            raise SafeWriteError("source changed before atomic replacement")
        os.replace(temporary, path)
        return diff
    finally:
        if "temporary" in locals() and temporary.exists():
            temporary.unlink()
        _unlock_file(lock_fd)
        os.close(lock_fd)
        # Lock removal is best-effort; the snapshot re-checks above remain
        # the correctness guard against a concurrent writer.
        with contextlib.suppress(OSError):
            os.unlink(lock)
