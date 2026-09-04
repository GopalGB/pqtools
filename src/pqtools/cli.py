"""Command line interface with dry-run source edits by default."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import containers
from .containers import ContainerError
from .core import (
    MQueryError,
    _snapshot,
    check,
    dependencies,
    format_source,
    parse,
    rename,
    replace_source,
    update_file,
)
from .evaluate import evaluate

_CONTAINER_SUFFIXES = {".xlsx", ".pbix", ".pbit", ".pbip"}
_BIND_SUFFIXES = {".csv", ".json"}


def _source(path: Path) -> str:
    return _snapshot(path).data.decode("utf-8", "strict")


def _is_container(path: Path) -> bool:
    return path.suffix.lower() in _CONTAINER_SUFFIXES or path.is_dir()


def _print(value: Any, as_json: bool) -> None:
    if as_json or not isinstance(value, str):
        print(json.dumps(value, sort_keys=True, default=lambda item: item.as_dict()))
    else:
        print(value, end="" if value.endswith("\n") else "\n")


def _member_expression(member_text: str) -> str:
    """Strip a ``containers.split_shared()`` value down to its expression.

    `member_text` is always ``shared NAME = <expr>;`` (or without the
    trailing ``;`` in a malformed document) - a full ``SectionMember``, not
    a standalone expression `evaluate()` can parse on its own (``shared``
    is only valid inside a ``section``). Wrapping it in a throwaway section
    and re-parsing locates the exact token span of ``<expr>`` without ever
    guessing at raw text offsets - safe even if a quoted member name like
    ``#"a = b"`` contains an ``=`` character.
    """
    prefix = "section S; "
    wrapped = prefix + member_text
    try:
        parsed = parse(wrapped)
    except MQueryError as error:
        raise MQueryError(f"unable to isolate member expression: {error}") from error
    tokens = parsed["tokens"]
    equal_index = next(
        (index for index, token in enumerate(tokens) if token["kind"] == "Equal"),
        None,
    )
    if equal_index is None:
        raise MQueryError("unable to isolate member expression: no '=' found")
    start = int(tokens[equal_index]["end"])
    last = tokens[-1]
    end = int(last["start"]) if last["kind"] == "Semicolon" else int(last["end"])
    return wrapped[start:end].strip()


def _member_source(text: str, container: str, member: str) -> str:
    members = containers.split_shared(text, container)
    if member not in members:
        raise MQueryError(f"{container}: no shared member named {member!r}")
    return _member_expression(members[member])


def _eval_source(args: argparse.Namespace) -> str:
    if _is_container(args.file):
        sections = containers.read_sections(args.file)
        if args.member is not None:
            for section in sections:
                members = containers.split_shared(section.source, section.container)
                if args.member in members:
                    return _member_expression(members[args.member])
            raise MQueryError(f"no shared member named {args.member!r}")
        if len(sections) != 1:
            raise MQueryError(
                f"{args.file}: eval on a container with multiple sections "
                "requires --member"
            )
        return sections[0].source
    text = _source(args.file)
    if args.member is not None:
        return _member_source(text, str(args.file), args.member)
    return text


def _parse_bind(spec: str) -> tuple[str, Path]:
    name, separator, raw_path = spec.partition("=")
    if not separator or not name:
        raise MQueryError(f"--bind requires NAME=PATH, got {spec!r}")
    return name, Path(raw_path)


def _load_binding(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix not in _BIND_SUFFIXES:
        raise MQueryError(f"{path}: --bind supports only .csv or .json, not {suffix!r}")
    snapshot = _snapshot(path)
    try:
        text = snapshot.data.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise MQueryError(f"{path}: source must be valid UTF-8") from error
    if suffix == ".csv":
        return list(csv.DictReader(io.StringIO(text)))
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise MQueryError(f"{path}: invalid JSON") from error


def _print_csv(rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = list(rows[0].keys()) if rows else []
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        if list(row.keys()) != fieldnames:
            raise MQueryError(
                "--format csv requires every row to share the same columns"
            )
        writer.writerow(row)


def _container_backup(path: Path) -> Path:
    """Copy ``path`` to ``path.bak`` before it is rewritten.

    Editing a binary container is the one thing here that destroys the input
    if it goes wrong, and the failure would surface in Excel rather than in
    this process. A sidecar copy makes that recoverable without asking the
    user to have thought of it first.
    """
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_bytes(path.read_bytes())
    return backup


def _write_container_transform(args: argparse.Namespace) -> int:
    """Apply a transform to a container's M and write it back into the file."""
    sections = containers.read_sections(args.file)
    if len(sections) != 1:
        raise ContainerError(
            f"{args.file}: --write supports a container with exactly one "
            f"section, found {len(sections)}"
        )
    transform = _transform_for(args)
    new_source = transform(sections[0].source)
    backup = _container_backup(args.file)
    diff = containers.write_sections(args.file, new_source, write=True)
    if args.json:
        _print({"backup": str(backup), "diff": diff}, True)
    else:
        print(diff or "(no change)", end="" if diff else "\n")
        print(f"// backup: {backup}", file=sys.stderr)
    return 0


def _run_add(args: argparse.Namespace) -> int:
    """Add a new ``shared`` query to a container or section document."""
    if args.name is None or args.source is None:
        raise MQueryError("add requires --name and --source")
    if not _is_container(args.file):
        raise MQueryError("add currently supports .xlsx/.pbix/.pbit containers")
    sections = containers.read_sections(args.file)
    if len(sections) != 1:
        raise ContainerError(
            f"{args.file}: add supports a container with exactly one section, "
            f"found {len(sections)}"
        )
    existing = containers.split_shared(sections[0].source, sections[0].container)
    if args.name in existing:
        raise MQueryError(
            f"{args.file}: a query named {args.name!r} already exists - use "
            "replace-source to change it"
        )
    body = args.source.rstrip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    text = sections[0].source.rstrip()
    new_source = f"{text}\n\nshared {_quote_identifier(args.name)} = {body};\n"
    if not args.write:
        print(new_source)
        return 0
    backup = _container_backup(args.file)
    containers.write_sections(args.file, new_source, write=True)
    print(f"// added {args.name}; backup: {backup}", file=sys.stderr)
    return 0


def _quote_identifier(name: str) -> str:
    """``Name`` or ``#"Name With Spaces"`` - M's two identifier spellings."""
    if (
        name
        and (name[0].isalpha() or name[0] == "_")
        and all(ch.isalnum() or ch == "_" for ch in name)
    ):
        return name
    return '#"' + name.replace('"', '""') + '"'


def _preview_container_transform(args: argparse.Namespace) -> int:
    """Apply format/rename/replace-source to a container's M and print it.

    Nothing is written. This is the read-only half of "work with the queries
    inside a .pbix/.xlsx": you see exactly what the edit would produce, and
    can redirect it to a .pq file to keep it.
    """
    sections = containers.read_sections(args.file)
    transform = _transform_for(args)
    outputs = [transform(section.source) for section in sections]
    if args.json:
        _print(
            [
                {"section": section.path, "source": output}
                for section, output in zip(sections, outputs, strict=True)
            ],
            True,
        )
    else:
        for section, output in zip(sections, outputs, strict=True):
            if len(sections) > 1:
                print(f"// ---- {section.path} ----")
            print(output)
    return 0


def _transform_for(args: argparse.Namespace) -> Callable[[str], str]:
    """The source-to-source function a write-shaped command asks for."""
    if args.command == "format":
        return format_source
    if args.command == "rename":
        if args.old is None or args.new is None:
            raise MQueryError("rename requires --old and --new")
        old, new = args.old, args.new
        return lambda text: rename(text, old, new)
    if args.source is None:
        raise MQueryError("replace-source requires --source")
    replacement = args.source
    return lambda text: replace_source(text, replacement)


def _run_list(args: argparse.Namespace) -> int:
    """Show the queries a file holds, so the next command has a name to use.

    Answers the first question anyone has about a .pbix or .xlsx they did not
    write: what is in here? Every name printed is directly usable as
    `pq eval FILE --member NAME`.
    """
    if _is_container(args.file):
        sections = containers.read_sections(args.file)
    else:
        text = _source(args.file)
        sections = [
            containers.QuerySection(
                container=str(args.file),
                path=str(args.file),
                source=text,
                kind="source",
            )
        ]
    entries: list[dict[str, Any]] = []
    for section in sections:
        try:
            members = containers.split_shared(section.source, section.container)
        except MQueryError:
            # A section that will not split is still worth reporting - staying
            # silent about it would make the file look emptier than it is.
            members = {}
        for name, source in members.items():
            entries.append(
                {
                    "name": name,
                    "section": section.path,
                    "lines": source.count("\n") + 1,
                }
            )
    if args.json:
        _print(entries, True)
    elif not entries:
        print("no queries found", file=sys.stderr)
    else:
        width = max(len(item["name"]) for item in entries)
        for item in entries:
            print(
                f"{item['name']:<{width}}  {item['lines']:>4} lines  {item['section']}"
            )
    return 0


def _run_eval(args: argparse.Namespace) -> int:
    source = _eval_source(args)
    bindings: dict[str, Any] = {}
    for spec in args.bind or []:
        name, path = _parse_bind(spec)
        bindings[name] = _load_binding(path)
    result = evaluate(source, bindings=bindings)
    if args.format == "csv":
        if not isinstance(result, list) or not all(
            isinstance(row, dict) for row in result
        ):
            raise MQueryError(
                "--format csv requires the result to be a table (a list of records)"
            )
        _print_csv(result)
    else:
        _print(result, True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pq")
    parser.add_argument(
        "command",
        choices=[
            "parse",
            "format",
            "check",
            "dependencies",
            "rename",
            "replace-source",
            "eval",
            "list",
            "add",
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
    parser.add_argument("--name", help="name of the query to add (add)")
    parser.add_argument(
        "--source", help="complete replacement source for replace-source"
    )
    parser.add_argument(
        "--member", help="the shared member to evaluate (eval on a section document)"
    )
    parser.add_argument(
        "--bind",
        action="append",
        metavar="NAME=PATH",
        help="bind a let-binding name to a .csv or .json data file (eval, repeatable)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="eval output format - csv requires a table result",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            return _run_list(args)
        if args.command == "add":
            return _run_add(args)
        if args.command == "eval":
            return _run_eval(args)
        if _is_container(args.file):
            if args.command in {"format", "rename", "replace-source"}:
                # Preview is always allowed: it rewrites nothing, so the
                # reason writing is gated does not apply to it. Seeing the
                # would-be result is most of the value and none of the risk.
                if not args.write:
                    return _preview_container_transform(args)
                return _write_container_transform(args)
            sections = containers.read_sections(args.file)
            if args.command == "check":
                diagnostics = [
                    diagnostic
                    for section in sections
                    for diagnostic in check(
                        section.source, f"{section.container}!{section.path}"
                    )
                ]
                if args.json:
                    _print([item.as_dict() for item in diagnostics], True)
                else:
                    for item in diagnostics:
                        print(
                            f"{item.file}:{item.line}:{item.column}: "
                            f"{item.severity} {item.code}: {item.message}"
                        )
                return 2 if any(item.severity == "error" for item in diagnostics) else 0
            if args.command == "parse":
                _print(
                    [
                        {
                            "file": f"{section.container}!{section.path}",
                            "parsed": parse(section.source),
                        }
                        for section in sections
                    ],
                    True,
                )
                return 0
            if args.command == "dependencies":
                _print(
                    [
                        {
                            "file": f"{section.container}!{section.path}",
                            "dependencies": dependencies(section.source),
                        }
                        for section in sections
                    ],
                    True,
                )
                return 0
        if args.command == "parse":
            _print(parse(_source(args.file)), True)
            return 0
        if args.command == "check":
            diagnostics = check(_source(args.file), str(args.file))
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
            _print(dependencies(_source(args.file)), True)
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
        if args.json:
            print(
                json.dumps({"code": code, "message": str(error)}, sort_keys=True),
                file=sys.stderr,
            )
        else:
            print(f"error {code}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
