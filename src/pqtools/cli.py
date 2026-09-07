"""Command line interface with dry-run source edits by default."""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime
import difflib
import glob
import io
import json
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import catalog, containers
from .builtins._shared import _DeferredRows, _type_name
from .containers import ContainerError
from .core import (
    Diagnostic,
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
from .evaluate import BUILTINS, evaluate
from .io import IOPolicy

# Suffixes with a structured reading. Everything else binds to raw bytes,
# which is what File.Contents returns - so there is no file --bind refuses.
_BIND_TABLE_SUFFIXES = {".xlsx", ".xlsm", ".xlsb"}

# The read-only verbs that accept more than one file - a plain path, several
# paths, or a shell-quoted glob the CLI expands itself (the shell cannot
# expand 'src/**/*.pq' when it is quoted, which is exactly why CI passes it
# quoted). Every other verb either writes (single-file only, enforced in
# `main()`) or - `eval`, `diff`, `explain` - takes something other than an
# arbitrary-length file list.
_BATCH_VERBS = {"parse", "check", "format", "dependencies", "show", "list"}


def _source(path: Path) -> str:
    return _snapshot(path).data.decode("utf-8", "strict")


# Both live in containers.py, beside `split_shared`, which produces what
# `member_expression` takes apart. They were copied here and into export.py
# while those lanes were built in parallel; one home now.
_is_container = containers.is_container
_member_expression = containers.member_expression


def _unprintable(item: Any) -> Any:
    """`json.dumps` fallback: convert what we can, refuse what we must not print.

    A `Sql.Database` navigation row's `Data` is a lazy `_DeferredRows` - it
    is not read until a query selects that table, which is the whole point of
    it. Printing the navigation table used to reach this fallback, find no
    `as_dict`, and hand the caller a raw `AttributeError` traceback; the CSV
    path was worse, writing a placeholder into a data cell as though it were
    a value. Forcing it here would be wrong too: that is exactly the "read
    every table in the catalog" behaviour the deferral exists to prevent.

    So it refuses, in the typed way, and says which step to write instead.
    """
    if isinstance(item, _DeferredRows):
        raise MQueryError(
            "this result contains a table that has not been read "
            f"({item!r}). Printing it would run a query per table in the "
            "catalog. Select the one you want first, for example "
            'Source{[Schema="dbo", Item="Orders"]}[Data]'
        )
    if isinstance(item, (bytes, datetime.date, datetime.time, datetime.timedelta)):
        # `--format csv` already writes these via `str()` (`csv.DictWriter`
        # stringifies any non-string cell) - JSON has no binary/date/time/
        # duration type of its own either, so this reuses the exact same
        # text rather than inventing a second, different convention for the
        # same value. `--set-param StartDate='#date(2024,1,1)'` is what
        # surfaced the gap: before this, ANY query whose result contained a
        # bare date/time/duration/binary value crashed JSON output with a
        # raw AttributeError instead of printing it.
        return str(item)
    try:
        return item.as_dict()
    except AttributeError:
        # Every value this evaluator is documented to produce is handled
        # above or serializes natively; reaching here means a value type
        # `_print` cannot represent in JSON - not this project's "read the
        # wrong thing silently" failure mode, but the same principle: name
        # it and refuse rather than let a bare AttributeError traceback
        # stand in for an error message.
        raise MQueryError(
            f"cannot represent a {_type_name(item)} result as JSON ({item!r})"
        ) from None


def _print(value: Any, as_json: bool) -> None:
    if as_json or not isinstance(value, str):
        print(json.dumps(value, sort_keys=True, default=_unprintable))
    else:
        print(value, end="" if value.endswith("\n") else "\n")


def _member_source(text: str, container: str, member: str) -> str:
    members = containers.split_shared(text, container)
    if member not in members:
        raise MQueryError(f"{container}: no shared member named {member!r}")
    return _member_expression(members[member])


def _eval_source(file: Path, member: str | None) -> str:
    """The M source `eval` runs and `show` prints - `show` never calls
    `evaluate()`, so this function is the one place that slicing has to be
    correct; both verbs use it so there is only one place to get it right.
    """
    if _is_container(file):
        sections = containers.read_sections(file)
        if member is not None:
            for section in sections:
                members = containers.split_shared(section.source, section.container)
                if member in members:
                    return _member_expression(members[member])
            raise MQueryError(f"no shared member named {member!r}")
        if len(sections) != 1:
            raise MQueryError(
                f"{file}: eval on a container with multiple sections requires --member"
            )
        return sections[0].source
    text = _source(file)
    if member is not None:
        return _member_source(text, str(file), member)
    return text


def _expand_one(token: str) -> list[Path]:
    """One CLI positional token to the file(s) it names.

    A token with glob metacharacters is expanded and MUST match at least
    one file: a glob that silently matches nothing would return a clean
    zero-file "success", which is exactly the failure this repo's own
    standing rule warns against (a clean zero must be positive-controlled,
    not trusted). A token WITHOUT glob syntax is returned as a literal path
    even when it does not exist, so the ordinary per-file "no such file"
    error still names the path the user actually typed rather than a
    glob-shaped message that would not apply to a plain filename.
    """
    has_glob_syntax = any(character in token for character in "*?[")
    matches = sorted(Path(match) for match in glob.glob(token, recursive=True))
    if matches:
        return matches
    if has_glob_syntax and not Path(token).exists():
        raise MQueryError(f"no files matched glob {token!r}")
    # An existing file whose NAME contains glob syntax - `report[1].pq` is
    # what Windows and every downloads folder produce - reaches here because
    # `glob.glob` read the brackets as a character class and matched nothing.
    # The file is right there; refusing it as an unmatched glob would be a
    # confident wrong answer about a path the user typed correctly.
    return [Path(token)]


def _expand_targets(tokens: list[str]) -> list[Path]:
    files: list[Path] = []
    for token in tokens:
        files.extend(_expand_one(token))
    return files


def _parse_bind(spec: str) -> tuple[str, Path]:
    name, separator, raw_path = spec.partition("=")
    if not separator or not name:
        raise MQueryError(f"--bind requires NAME=PATH, got {spec!r}")
    return name, Path(raw_path)


def _load_binding(path: Path) -> Any:
    """Read PATH into the value the bound step should produce.

    The rule is "whatever pqtools would get from this file", so a bind lines
    up with the step it replaces: a `.csv` step yields records, a `.json` step
    the parsed document, a workbook step the Excel.Workbook navigation table,
    and anything else the bytes File.Contents returns.

    That last case is why there is no longer a supported-suffix list.
    Refusing `.xlsx` was the visible symptom - Excel.Workbook is a supported
    connector, so the one file type a workbook-hosted query is most likely to
    reference was also the one --bind would not accept - but the refusal was
    wrong in general, since File.Contents happily reads any file at all.
    """
    suffix = path.suffix.lower()
    snapshot = _snapshot(path)
    if suffix in _BIND_TABLE_SUFFIXES:
        from .builtins._sources import workbook_nav_table

        # useHeaders=False, matching Excel.Workbook's own default. Generated
        # queries pass null here and then call Table.PromoteHeaders
        # themselves, so promoting during the bind would promote twice and
        # lose the real header row - which surfaces later and confusingly, as
        # "column not found" against a column the file plainly has.
        return workbook_nav_table(snapshot.data, False)
    if suffix not in {".csv", ".json"}:
        return snapshot.data
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


def _parse_set_param(spec: str) -> tuple[str, str]:
    name, separator, raw_value = spec.partition("=")
    if not separator or not name:
        raise MQueryError(f"--set-param requires NAME=VALUE, got {spec!r}")
    return name, raw_value


def _eval_param_literal(name: str, text: str, io_policy: IOPolicy) -> Any:
    """Bind NAME to the M VALUE parses to - through this package's own
    parser and evaluator, the same pipeline the query body itself runs on.

    That is what turns ``--set-param StartDate='#date(2024,1,1)'`` into a
    real M date rather than the seventeen characters between the quotes:
    `evaluate()` is handed the text exactly as `pq eval` hands it the query
    source, so whatever the query's own `#date(...)`/`"..."`/`123` would
    mean, this means the same thing. There is no separate literal parser to
    keep in sync with the real one, and no Python `eval` - VALUE is M, not
    Python, and only the M grammar gets to say what it means.

    Reusing the real evaluator means VALUE is not restricted to literals -
    `File.Contents(...)` in a --set-param does what it says. That is stated
    rather than fenced off, for two reasons. It grants nothing new: the
    query body could already call it, so there is no boundary here to cross.
    And `io_policy` is threaded through, so the network and database gates
    apply to VALUE exactly as they apply to the query - a --set-param naming
    `Web.Contents` is refused without `--allow-net`, which is the property
    that would actually matter if VALUE ever came from somewhere other than
    the operator's own command line.
    """
    try:
        return evaluate(text, io=io_policy)
    except MQueryError as error:
        raise MQueryError(
            f"--set-param {name}: {text!r} is not a valid M value: {error}"
        ) from error


def _reject_deferred(value: Any, column: str) -> None:
    """Refuse an unread table anywhere inside a cell, at any depth.

    The first version of this check looked at top-level cells only, so a
    deferred value one level down - `Table.Group(Sql.Database(...), ...)`
    nests the navigation rows inside a cell - was handed to
    `csv.DictWriter`, which `str()`s it into `<deferred ...>`. That is
    precisely the "a table nobody read is indistinguishable from a value
    somebody measured" case the check was written to stop, and the comment
    said so while the code did not do it. The JSON path never had the bug
    because `json.dumps(default=...)` recurses on its own.
    """
    if isinstance(value, _DeferredRows):
        raise MQueryError(
            f"column {column!r} holds a table that has not been read "
            f"({value!r}); --format csv cannot represent it. Select the one "
            'you want first, for example Source{[Schema="dbo", '
            'Item="Orders"]}[Data]'
        )
    if isinstance(value, dict):
        for item in value.values():
            _reject_deferred(item, column)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_deferred(item, column)


def _print_csv(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        for column, cell in row.items():
            _reject_deferred(cell, column)
    fieldnames: list[str] = list(rows[0].keys()) if rows else []
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        if list(row.keys()) != fieldnames:
            raise MQueryError(
                "--format csv requires every row to share the same columns"
            )
        writer.writerow(row)


# A container is rewritten in place, so its backup is the only copy of the
# pre-edit state. That makes the backup itself a thing worth protecting:
# neither an unrelated file nor an EARLIER backup may be destroyed to create
# it. Both were possible - `Path.write_bytes` follows a symlink at the
# destination and truncates whatever it finds, so a `book.xlsx.bak` symlink
# pointed at someone else's file overwrote that file, and a second edit
# silently replaced the original backup with the already-once-edited copy.
_MAX_BACKUPS = 100


def _container_backup(path: Path) -> Path:
    """Snapshot ``path`` to the first free ``path.bak[.N]``, or refuse.

    Editing a binary container is the one thing here that destroys the input
    if it goes wrong, and the failure would surface in Excel rather than in
    this process. A sidecar copy makes that recoverable without asking the
    user to have thought of it first.

    Preservation-first: an existing backup is NEVER overwritten, because it
    holds a state this run cannot reconstruct. The next free numbered sidecar
    is used instead. The destination is opened with ``O_CREAT | O_EXCL``
    (plus ``O_NOFOLLOW`` where the platform has it), which in one atomic
    operation refuses a symlink, refuses an existing file, and settles the
    race with anything creating the same name concurrently.

    A backup that cannot be completed raises, and every caller creates the
    backup BEFORE touching the container, so a failure here leaves the
    original file untouched.
    """
    data = path.read_bytes()
    base = path.with_suffix(path.suffix + ".bak")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for index in range(_MAX_BACKUPS):
        candidate = base if index == 0 else Path(f"{base}.{index}")
        try:
            handle = os.open(candidate, flags, 0o600)
        except FileExistsError:
            # Also the symlink case: O_EXCL fails on a symlink whatever it
            # points at, so the target is never opened, let alone written.
            continue
        except OSError as error:
            raise MQueryError(f"{candidate}: cannot create backup: {error}") from error
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            # A half-written backup is worse than none - it looks recoverable.
            with contextlib.suppress(OSError):
                candidate.unlink()
            raise MQueryError(
                f"{candidate}: backup failed, {path} was not modified: {error}"
            ) from error
        with contextlib.suppress(OSError):
            candidate.chmod(stat.S_IMODE(path.stat().st_mode))
        return candidate
    raise MQueryError(
        f"{base} and {_MAX_BACKUPS - 1} numbered sidecars all exist; "
        f"{path} was not modified. Move or delete the old backups first."
    )


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


def _run_list(files: list[Path], args: argparse.Namespace) -> int:
    """Show the queries a file holds, so the next command has a name to use.

    Answers the first question anyone has about a .pbix or .xlsx they did not
    write: what is in here? Every name printed is directly usable as
    `pq eval FILE --member NAME`.

    `files` is always the glob-expanded list (length one for a plain
    ``pq list FILE``), so a bad file among several is reported and skipped
    rather than losing the names the good files already found.
    """
    records: list[dict[str, Any]] = []
    worst = 0
    for path in files:
        try:
            if _is_container(path):
                sections = containers.read_sections(path)
            else:
                text = _source(path)
                sections = [
                    containers.QuerySection(
                        container=str(path),
                        path=str(path),
                        source=text,
                        kind="source",
                    )
                ]
        except (OSError, UnicodeDecodeError, MQueryError) as error:
            code = getattr(error, "code", "M_IO_ERROR")
            print(f"{path}: error {code}: {error}", file=sys.stderr)
            worst = 2
            # Also into the JSON. Reporting the failure on stderr alone left
            # `pq list --json` printing a complete-looking array on stdout
            # with nothing in it saying a file had failed - every other batch
            # verb appends this record, and a consumer reading only stdout
            # would have believed the list was the whole answer.
            # One list, in argument order. The first cut appended this
            # straight into the names list, which the plain-text branch
            # formats with `item["name"]` - so every read failure under a
            # non-JSON `pq list` died with a bare KeyError. Splitting it into
            # a second list fixed that but moved every failure to the END of
            # the --json array, so a consumer could no longer line records up
            # against the files it passed. One list filtered on "name" does
            # both jobs.
            records.append(
                {"file": str(path), "error": {"code": code, "message": str(error)}}
            )
            continue
        for section in sections:
            try:
                members = containers.split_shared(section.source, section.container)
            except MQueryError as error:
                # This comment used to say a section that will not split is
                # "still worth reporting" - directly above a line that
                # reported nothing. `members = {}` made an unparseable
                # section indistinguishable from an empty one, so `pq list`
                # on a file `pq check` rejects with M_PARSE_ERROR printed
                # "no queries found" and exited 0. Silently returning a
                # shorter list is the one thing this package promises never
                # to do.
                code = getattr(error, "code", "M_PARSE_ERROR")
                print(f"{section.path}: error {code}: {error}", file=sys.stderr)
                worst = 2
                records.append(
                    {
                        "file": section.path,
                        "error": {"code": code, "message": str(error)},
                    }
                )
                continue
            for name, source in members.items():
                records.append(
                    {
                        "name": name,
                        "section": section.path,
                        "lines": source.count("\n") + 1,
                    }
                )
    named = [item for item in records if "name" in item]
    if args.json:
        _print(records, True)
    elif named:
        width = max(len(item["name"]) for item in named)
        for item in named:
            print(
                f"{item['name']:<{width}}  {item['lines']:>4} lines  {item['section']}"
            )
    elif worst == 0:
        # Only when nothing failed. Printing "no queries found" underneath a
        # file that had just reported an error told the reader the file was
        # empty when it was in fact unreadable.
        print("no queries found", file=sys.stderr)
    return worst


def _check_diagnostics(path: Path) -> list[Diagnostic]:
    if _is_container(path):
        sections = containers.read_sections(path)
        return [
            diagnostic
            for section in sections
            for diagnostic in check(
                section.source, f"{section.container}!{section.path}"
            )
        ]
    return check(_source(path), str(path))


def _parse_records(path: Path) -> list[dict[str, Any]]:
    if _is_container(path):
        sections = containers.read_sections(path)
        return [
            {
                "file": f"{section.container}!{section.path}",
                "parsed": parse(section.source),
            }
            for section in sections
        ]
    return [{"file": str(path), "parsed": parse(_source(path))}]


def _dependencies_records(path: Path) -> list[dict[str, Any]]:
    if _is_container(path):
        sections = containers.read_sections(path)
        return [
            {
                "file": f"{section.container}!{section.path}",
                "dependencies": dependencies(section.source),
            }
            for section in sections
        ]
    return [{"file": str(path), "dependencies": dependencies(_source(path))}]


def _run_check_batch(files: list[Path], args: argparse.Namespace) -> int:
    """`pq check` over more than one resolved file.

    A single file never reaches this function - see `main()` - so there is
    no existing single-file rendering to match here, only the batch
    contract: one exit code for the whole run (the worst of any file) and
    at least one line per file, including a clean one, so
    `pq check 'src/**/*.pq' | grep error` (and the absence of a hit) both
    mean what they look like they mean.
    """
    worst = 0
    json_diagnostics: list[dict[str, Any]] = []
    for path in files:
        try:
            diagnostics = _check_diagnostics(path)
        except (OSError, UnicodeDecodeError, MQueryError) as error:
            code = getattr(error, "code", "M_IO_ERROR")
            worst = 2
            if args.json:
                json_diagnostics.append(
                    Diagnostic(file=str(path), code=code, message=str(error)).as_dict()
                )
            else:
                print(f"{path}: error {code}: {error}", file=sys.stderr)
            continue
        if any(item.severity == "error" for item in diagnostics):
            worst = 2
        if args.json:
            json_diagnostics.extend(item.as_dict() for item in diagnostics)
        elif diagnostics:
            for item in diagnostics:
                print(
                    f"{item.file}:{item.line}:{item.column}: "
                    f"{item.severity} {item.code}: {item.message}"
                )
        else:
            print(f"{path}: OK")
    if args.json:
        _print(json_diagnostics, True)
    return worst


def _run_parse_batch(files: list[Path], args: argparse.Namespace) -> int:
    """`pq parse` over more than one file - always JSON, like the single-file
    form (parse's result is a tree, not text; there is no text rendering to
    fall back to, batch or not).
    """
    worst = 0
    records: list[dict[str, Any]] = []
    for path in files:
        try:
            records.extend(_parse_records(path))
        except (OSError, UnicodeDecodeError, MQueryError) as error:
            code = getattr(error, "code", "M_IO_ERROR")
            records.append(
                {"file": str(path), "error": {"code": code, "message": str(error)}}
            )
            worst = 2
    _print(records, True)
    return worst


def _run_dependencies_batch(files: list[Path], args: argparse.Namespace) -> int:
    worst = 0
    records: list[dict[str, Any]] = []
    for path in files:
        try:
            records.extend(_dependencies_records(path))
        except (OSError, UnicodeDecodeError, MQueryError) as error:
            code = getattr(error, "code", "M_IO_ERROR")
            records.append(
                {"file": str(path), "error": {"code": code, "message": str(error)}}
            )
            worst = 2
    _print(records, True)
    return worst


def _run_format_batch(files: list[Path], args: argparse.Namespace) -> int:
    """`pq format` (no ``--write``) over more than one file - preview only.

    ``--write`` already refused a multi-file batch upstream (writing verbs
    stay single-file), so every path here is read-only: a diff for a plain
    source file, matching the single-file dry run exactly
    (`update_file(..., write=False)` is the same call that makes), and the
    raw formatted section(s) for a container, matching
    `_preview_container_transform`. This only adds the "more than one FILE"
    layer on top of rendering that already exists per file.
    """
    worst = 0
    json_records: list[dict[str, Any]] = []
    for path in files:
        try:
            if _is_container(path):
                sections = containers.read_sections(path)
                outputs = [
                    (section.path, format_source(section.source))
                    for section in sections
                ]
            else:
                outputs = [(str(path), update_file(path, format_source, write=False))]
        except (OSError, UnicodeDecodeError, MQueryError) as error:
            code = getattr(error, "code", "M_IO_ERROR")
            worst = 2
            if args.json:
                json_records.append(
                    {"file": str(path), "error": {"code": code, "message": str(error)}}
                )
            else:
                print(f"{path}: error {code}: {error}", file=sys.stderr)
            continue
        for name, text in outputs:
            if args.json:
                json_records.append({"file": name, "source": text})
            else:
                print(f"// ---- {name} ----")
                display = text or "(no change)"
                print(display, end="" if display.endswith("\n") else "\n")
    if args.json:
        _print(json_records, True)
    return worst


def _run_show(files: list[Path], args: argparse.Namespace) -> int:
    """``pq show FILE [--member NAME]`` - print raw M source, never run it.

    Reuses `_eval_source` - the exact slicing `eval` uses to pick what to
    run - and stops there. `evaluate()` never appears in this function's
    call graph, which is not an implementation detail: it is the guarantee
    the verb exists to make for someone reading an unfamiliar file that
    might otherwise reach Csv.Document/Web.Contents/Sql.Database before
    they meant to run anything.

    A single file (the common case) prints the bare source - `--json` wraps
    it in one JSON string, matching every other single-file JSON output in
    this module (`eval`, `parse`, ...) rather than a one-element array
    nothing else here would produce. The array-of-records shape is reserved
    for an actual multi-file batch, where a bare string would not say which
    file it came from.
    """
    if len(files) == 1:
        text = _eval_source(files[0], args.member)
        _print(text, args.json)
        return 0
    worst = 0
    records: list[dict[str, Any]] = []
    for path in files:
        try:
            text = _eval_source(path, args.member)
        except (OSError, UnicodeDecodeError, MQueryError) as error:
            code = getattr(error, "code", "M_IO_ERROR")
            worst = 2
            if args.json:
                records.append(
                    {"file": str(path), "error": {"code": code, "message": str(error)}}
                )
            else:
                print(f"{path}: error {code}: {error}", file=sys.stderr)
            continue
        if args.json:
            records.append({"file": str(path), "source": text})
            continue
        print(f"// ---- {path} ----")
        print(text, end="" if text.endswith("\n") else "\n")
    if args.json:
        _print(records, True)
    return worst


def _diff_source(path: Path) -> str:
    """The FORMATTED M source `diff` compares.

    Formatted rather than raw, so a whitespace-only reflow does not drown
    the change that matters - this is `pq diff`'s documented choice (see
    its ``--help`` text). A container reuses `eval`/`show`'s own
    single-section rule: with more than one section, which one to compare
    is ambiguous, and picking one silently would be a guess, not a diff.
    """
    if _is_container(path):
        sections = containers.read_sections(path)
        if len(sections) != 1:
            raise MQueryError(
                f"{path}: diff on a container with multiple sections is not "
                "supported - extract the one section you want first"
            )
        return format_source(sections[0].source)
    return format_source(_source(path))


def _run_diff(args: argparse.Namespace) -> int:
    left, right = args.file
    left_text = _diff_source(left)
    right_text = _diff_source(right)
    diff = "".join(
        difflib.unified_diff(
            left_text.splitlines(keepends=True),
            right_text.splitlines(keepends=True),
            fromfile=str(left),
            tofile=str(right),
        )
    )
    if args.json:
        _print({"identical": not diff, "diff": diff}, True)
    else:
        sys.stdout.write(diff)
    return 0 if not diff else 1


def _run_explain(args: argparse.Namespace) -> int:
    """``pq explain NAME`` - why pqtools refuses NAME, or that it does not.

    Consults the same two facts `evaluate()` itself consults when an
    identifier resolves to neither a local binding nor a builtin: is it
    implemented (`BUILTINS`), and if not, is it a documented Power Query
    function pqtools has deliberately not implemented (`catalog.explain`).
    Nothing here is a second copy of that judgment - both are imported, not
    re-derived. (Three names - `SharePoint.Files/Tables/Contents` - get a
    more specific "reach it via --bind" message from `evaluate()` at run
    time than the generic connector reason catalog.py gives here; both say
    the same thing, only the wording differs, so this is not treated as a
    second source of truth to reconcile.)
    """
    name: str = args.file
    if name in BUILTINS:
        message = f"{name} is implemented by pqtools - it is not refused."
        supported = True
    else:
        reason = catalog.explain(name)
        supported = False
        if reason is not None:
            message = reason
        else:
            message = (
                f"{name} is not a name pqtools recognizes as a documented "
                "Power Query M function. It may be a typo, an internal or "
                "undocumented name, or something outside the M standard "
                "library (a query name, a variable, a record field)."
            )
    if args.json:
        _print({"name": name, "supported": supported, "message": message}, True)
    else:
        print(message)
    return 0


def _io_policy(args: argparse.Namespace) -> IOPolicy:
    """Turn the --allow-* flags into the policy the evaluator enforces.

    Default-deny is the whole point: the M source, not the person running it,
    supplies the URL a connector reaches, and that source often came from
    somebody else's workbook. Naming the flag in the refusal keeps the honest
    case one word away without making the hostile case free.
    """
    return IOPolicy(
        allow_net=bool(getattr(args, "allow_net", False)),
        allow_db=bool(getattr(args, "allow_db", False)),
        allow_private=bool(getattr(args, "allow_private_net", False)),
        hosts=frozenset(h.lower() for h in (getattr(args, "allow_host", None) or [])),
    )


def _export_result(result: Any, fmt: str, out: str | None) -> int:
    """`--to parquet --out FILE`. Columnar formats need a file, not a pipe.

    `--format json|csv` still owns stdout. `--to` exists for the one thing a
    stream cannot carry: a typed, columnar file. There is deliberately no
    `--to pandas` - a DataFrame is a Python object, and a process that exits
    has nowhere to put one; that is `pqtools.to_pandas()`, from Python.
    """
    from .export import to_parquet

    assert out is not None  # _run_eval refuses before evaluating
    if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
        raise MQueryError(f"--to {fmt} requires the result to be a table")
    to_parquet(result, Path(out))
    return 0


def _run_eval(args: argparse.Namespace) -> int:
    # Checked before the query runs, not after. Evaluating a slow query to
    # completion and only then reporting a missing --out wastes the whole run
    # on a mistake that was visible in the command line.
    if args.to and args.out is None:
        raise MQueryError(f"--to {args.to} writes a file: pass --out PATH")
    source = _eval_source(args.file, args.member)
    io_policy = _io_policy(args)
    bindings: dict[str, Any] = {}
    for spec in args.bind or []:
        name, path = _parse_bind(spec)
        bindings[name] = _load_binding(path)
    # --set-param last: a name bound by both --bind and --set-param takes the
    # scalar, since --set-param is the more specific ask (one value, named on
    # the command line) rather than a first-match-wins ordering accident.
    for spec in args.set_param or []:
        name, text = _parse_set_param(spec)
        bindings[name] = _eval_param_literal(name, text, io_policy)
    result = evaluate(source, bindings=bindings, io=io_policy)
    if args.to:
        return _export_result(result, args.to, args.out)
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


def _normalise_targets(args: argparse.Namespace) -> None:
    """Give `args.file` the shape each verb's own code expects.

    Argparse fixes a positional's arity when it is declared, so per-verb
    arity cannot be expressed there without knowing the verb first - and
    learning the verb by peeking at `argv[0]` is wrong, because options may
    precede it. So the positional is declared once as a list and reshaped
    here, after argparse has identified the command properly.
    """
    targets: list[str] = [str(item) for item in args.file]
    verb = args.command
    if verb == "explain":
        if len(targets) != 1:
            raise MQueryError(
                "explain takes exactly one NAME, e.g. pq explain Table.Group"
            )
        args.file = targets[0]
        return
    if verb == "diff":
        if len(targets) != 2:
            raise MQueryError("diff takes exactly two files: pq diff A.pq B.pq")
        args.file = [Path(item) for item in targets]
        return
    if verb in _BATCH_VERBS:
        if not targets:
            raise MQueryError(f"{verb} needs at least one file")
        args.file = targets
        return
    if len(targets) != 1:
        raise MQueryError(f"{verb} takes exactly one file, got {len(targets)}")
    args.file = Path(targets[0])


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
            "show",
            "explain",
            "diff",
        ],
        help=(
            "show: print a member's raw M source without evaluating it. "
            "explain NAME: report whether pqtools implements NAME, or why "
            "it refuses it. diff A B: unified diff of two files' FORMATTED "
            "M source (formatting-only differences are ignored - see "
            "--help on `diff` itself for why). parse/check/format "
            "(without --write)/dependencies/show/list accept multiple "
            "files or a glob."
        ),
    )
    # ONE positional, always a list, arity checked after parsing by
    # `_normalise_targets`. An earlier cut chose the positional's shape from
    # `argv[0]`, which argparse itself has never required the verb to be:
    # `pq --json check f.pq` is valid and made the peek read "--json" as the
    # command, so `check` got a single Path where it expected a list
    # (TypeError, bare traceback) and `explain` got a Path where it expected
    # a name - answering "not a name pqtools recognizes" about a function it
    # implements, and exiting 0. Option order must not be able to change the
    # parser's shape.
    parser.add_argument(
        "file",
        nargs="*",
        metavar="FILE",
        help="the file(s) to act on - one or more (or a quoted glob) for "
        "parse/check/format/dependencies/show/list, exactly two for diff, "
        "one for everything else. For `explain` this is a NAME, not a file.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--write",
        action="store_true",
        help="atomically replace source after validation",
    )
    parser.add_argument(
        "--allow-net",
        action="store_true",
        help="permit Web.Contents/OData.Feed to reach the network (off by default)",
    )
    parser.add_argument(
        "--allow-db",
        action="store_true",
        help="permit Sql.Database/Odbc/PostgreSQL/MySQL/Oracle connections",
    )
    parser.add_argument(
        "--allow-private-net",
        action="store_true",
        help="also permit loopback/private/link-local addresses (off by default: "
        "169.254.169.254 is the cloud metadata endpoint)",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        metavar="HOST",
        help="restrict --allow-net to this host (repeatable)",
    )
    parser.add_argument("--old")
    parser.add_argument("--new")
    parser.add_argument("--name", help="name of the query to add (add)")
    parser.add_argument(
        "--source", help="complete replacement source for replace-source"
    )
    parser.add_argument(
        "--member",
        help="the shared member to evaluate or show (eval/show on a section document)",
    )
    parser.add_argument(
        "--bind",
        action="append",
        metavar="NAME=PATH",
        help="bind a let-binding name to a .csv or .json data file (eval, repeatable)",
    )
    parser.add_argument(
        "--set-param",
        action="append",
        metavar="NAME=VALUE",
        help="bind a let-binding name to an M EXPRESSION, e.g. "
        "--set-param StartDate='#date(2024,1,1)' (eval, repeatable). "
        "Evaluated by pqtools' own M evaluator, never Python eval(), and "
        "under the same --allow-net/--allow-db policy as the query itself - "
        "so it is usually a literal, but any M the query could run, it can "
        "run too",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="eval output format - csv requires a table result",
    )
    parser.add_argument(
        "--to",
        choices=["parquet"],
        help="eval: write the result as a typed columnar file (needs --out). "
        "Requires the 'arrow' extra: pip install 'pqtools[arrow]'",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="eval: destination file for --to",
    )
    # `parse_known_args`, not `parse_args`, because argparse cannot split a
    # variable-length positional across an intervening option: given
    # `pq list --json a.pq b.pq` it matches `file` as empty and hands both
    # paths back as leftovers. They are the files, in order, so they are
    # folded back below - while a leftover that looks like an option is still
    # the unknown-option error argparse would have raised.
    args, extra = parser.parse_known_args(argv)
    if any(item.startswith("-") for item in extra):
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.file = [*args.file, *extra]
    try:
        _normalise_targets(args)
        if args.command == "explain":
            return _run_explain(args)
        if args.command == "diff":
            return _run_diff(args)
        if args.command in _BATCH_VERBS:
            # `args.file` is the raw token list here (see the parser setup
            # above) - expand it before anything downstream sees a "file".
            files = _expand_targets(args.file)
            if args.command == "format" and args.write and len(files) > 1:
                # "Half-applying" would mean writing whichever file happens
                # to be first and silently leaving the rest untouched - the
                # thing the PRD explicitly calls out as worse than refusing.
                raise MQueryError(
                    f"format --write does not support a {len(files)}-file "
                    "batch - writing verbs stay single-file; run it once "
                    "per file"
                )
            if args.command == "list":
                return _run_list(files, args)
            if args.command == "show":
                return _run_show(files, args)
            if len(files) > 1:
                if args.command == "check":
                    return _run_check_batch(files, args)
                if args.command == "parse":
                    return _run_parse_batch(files, args)
                if args.command == "dependencies":
                    return _run_dependencies_batch(files, args)
                if args.command == "format":
                    return _run_format_batch(files, args)
            # Exactly one resolved file (the common case: a plain filename,
            # or a glob that matched one thing) - fall through to the
            # single-file handling below, unchanged from before batch mode
            # existed, so every existing invocation still behaves exactly
            # as it always did.
            args.file = files[0]
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
