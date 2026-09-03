"""Command line interface with dry-run source edits by default."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .core import (
    MQueryError,
    check,
    dependencies,
    format_source,
    parse,
    rename,
    replace_source,
    update_file,
)


def _source(path: Path) -> str:
    limit = 10 * 1024 * 1024
    if path.stat().st_size > limit:
        raise MQueryError("input exceeds 10 MiB")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise MQueryError("input exceeds 10 MiB")
    return data.decode("utf-8", "strict")


def _print(value: Any, as_json: bool) -> None:
    if as_json or not isinstance(value, str):
        print(json.dumps(value, sort_keys=True, default=lambda item: item.as_dict()))
    else:
        print(value, end="" if value.endswith("\n") else "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mquery")
    parser.add_argument(
        "command",
        choices=[
            "parse",
            "format",
            "check",
            "dependencies",
            "rename",
            "replace-source",
        ],
    )
    parser.add_argument("file", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--write",
        action="store_true",
        help="atomically replace source after validation",
    )
    parser.add_argument("--old")
    parser.add_argument("--new")
    parser.add_argument(
        "--source", help="complete replacement source for replace-source"
    )
    args = parser.parse_args(argv)
    try:
        source = _source(args.file)
        if args.command == "parse":
            _print(parse(source), True)
            return 0
        if args.command == "check":
            diagnostics = check(source, str(args.file))
            if args.json:
                _print([item.as_dict() for item in diagnostics], True)
            else:
                for item in diagnostics:
                    print(
                        f"{item.file}:{item.line}:{item.column}: "
                        f"{item.severity} {item.code}: {item.message}"
                    )
            return 2 if any(item.severity == "error" for item in diagnostics) else 0
        if args.command == "dependencies":
            _print(dependencies(source), True)
            return 0
        transform: Callable[[str], str]
        if args.command == "format":
            transform = format_source
        elif args.command == "rename":
            if args.old is None or args.new is None:
                raise MQueryError("rename requires --old and --new")
            old, new = args.old, args.new

            def transform(text: str) -> str:
                return rename(text, old, new)

        else:
            if args.source is None:
                raise MQueryError("replace-source requires --source")
            replacement = args.source

            def transform(text: str) -> str:
                return replace_source(text, replacement)

        _print(update_file(args.file, transform, write=args.write), args.json)
        return 0
    except (OSError, UnicodeDecodeError, MQueryError) as error:
        code = getattr(error, "code", "M_IO_ERROR")
        _print(
            {"code": code, "message": str(error)}
            if args.json
            else f"error {code}: {error}",
            args.json,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
