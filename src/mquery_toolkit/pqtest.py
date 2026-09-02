"""Bounded wrapper for a user-installed PQTest executable."""

from __future__ import annotations

import platform
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from .core import (
    MAX_BYTES,
    NODE_TIMEOUT_SECONDS,
    AdapterError,
    _ProcessOutputLimit,
    _run_process_bounded,
)

PQTEST_VERSION = "2.155.2"
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(runner: Runner, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    if runner is subprocess.run:
        return _run_bounded(command)
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=NODE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise AdapterError("PQTest process failed") from error
    if (
        len(result.stdout.encode()) > MAX_BYTES
        or len(result.stderr.encode()) > MAX_BYTES
    ):
        raise AdapterError("PQTest output exceeds 10 MiB")
    if result.returncode:
        raise AdapterError(f"PQTest exited {result.returncode}")
    return result


def _run_bounded(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = _run_process_bounded(list(command), None, NODE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise AdapterError("PQTest timed out after 30 seconds") from error
    except _ProcessOutputLimit as error:
        raise AdapterError("PQTest output exceeds 10 MiB") from error
    except OSError as error:
        raise AdapterError("PQTest process failed") from error
    try:
        output = result.stdout.decode("utf-8", "strict")
        error_output = result.stderr.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise AdapterError("PQTest output is not valid UTF-8") from error
    if result.returncode:
        raise AdapterError(f"PQTest exited {result.returncode}")
    return subprocess.CompletedProcess(command, result.returncode, output, error_output)


def validate_pqtest(path: Path, runner: Runner = subprocess.run) -> Path:
    if platform.system() != "Windows":
        raise AdapterError("PQTest adapter is supported on Windows only")
    if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".exe":
        raise AdapterError("PQTest path must name a user-installed regular .exe")
    version = _run(runner, [str(path), "version"]).stdout
    if not re.search(rf"(?<![0-9.]){re.escape(PQTEST_VERSION)}(?![0-9.])", version):
        raise AdapterError(f"PQTest {PQTEST_VERSION} is required")
    return path


def run_pqtest(path: Path, args: Sequence[str], runner: Runner = subprocess.run) -> str:
    validate_pqtest(path, runner)
    return _run(runner, [str(path), *args]).stdout
