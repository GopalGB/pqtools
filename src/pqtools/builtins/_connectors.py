"""Local-file connectors: ``File.Contents``, ``Csv.Document``, ``Text.FromBinary``.

These used to be refused wholesale alongside ``Sql.Database`` and
``Web.Contents``, on the reasoning that connectors belong to Microsoft's
Mashup Engine. That reasoning was too broad. Reading a CSV off local disk is
not proprietary - it is reading a CSV - and ``Csv.Document(File.Contents(...))``
is the single most common ``Source`` step in real Power Query. Refusing it
forced every user through ``--bind`` for the one case that needs no help.

The boundary that remains is the honest one, and it is about *capability*,
not about the word "connector":

- **Implemented** - sources whose semantics are fully specified by a public
  format and need nothing but the local filesystem.
- **Still refused** - ``Sql.Database``, ``Web.Contents``, ``SharePoint.*``,
  ``Odbc.*`` and friends. These need credentials, a network identity,
  driver-specific type mapping, or query folding into a remote engine. An
  approximation there is not a convenience, it is a wrong answer wearing the
  right shape.

Owned by exactly one implementer; register new names in this module's own
``BUILTINS`` dict (see ``builtins/__init__.py``).
"""

from __future__ import annotations

import base64
import binascii
import gzip
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _require_int,
    _require_str,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx
else:  # pragma: no cover - runtime alias only
    _Ctx = Any


# Power Query identifies encodings by Windows code page (TextEncoding.Type).
# Only codecs Python ships are listed; an unlisted page raises rather than
# silently decoding as something else, because a wrong codec produces
# plausible-looking mojibake instead of an error.
_CODE_PAGES: dict[int, str] = {
    1200: "utf-16-le",
    1201: "utf-16-be",
    1252: "cp1252",
    10000: "mac-roman",
    20127: "ascii",
    28591: "latin-1",
    65000: "utf-7",
    65001: "utf-8",
}

_DEFAULT_ENCODING = 65001


def _decode(data: bytes, code_page: int | None) -> str:
    page = _DEFAULT_ENCODING if code_page is None else code_page
    codec = _CODE_PAGES.get(page)
    if codec is None:
        raise UnsupportedError(
            f"text encoding code page {page} (known: "
            + ", ".join(str(k) for k in sorted(_CODE_PAGES))
            + ")"
        )
    try:
        text = data.decode(codec)
    except UnicodeDecodeError as error:
        raise EvalError(f"cannot decode input as code page {page}: {error}") from error
    # A UTF-8 BOM survives decoding as U+FEFF and would otherwise become part
    # of the first column's name, which is a classic silent header corruption.
    return text.lstrip("\ufeff") if text.startswith("\ufeff") else text


def _as_text(value: Any, what: str, code_page: int | None) -> str:
    if isinstance(value, bytes):
        return _decode(value, code_page)
    if isinstance(value, str):
        return value
    raise EvalError(f"{what}: expected binary or text, got {_type_name(value)}")


def _file_contents(args: list[Any], ctx: _Ctx) -> Any:
    _arity("File.Contents", args, 1, 2)
    raw = _require_str(args[0])
    path = Path(raw).expanduser()
    if not path.exists():
        # Real queries carry the authoring machine's absolute path
        # (C:\Users\...), which will not exist anywhere else. Naming --bind
        # here turns a dead end into the next step.
        raise EvalError(
            f"File.Contents: no such file: {raw} - if this path came from the "
            "machine that authored the query, bind the step's result instead: "
            "--bind Source=<local file>"
        )
    if path.is_dir():
        raise EvalError(f"File.Contents: not a file: {raw}")
    return path.read_bytes()


def _text_from_binary(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.FromBinary", args, 1, 2)
    code_page = None if len(args) < 2 or args[1] is None else _require_int(args[1])
    value = args[0]
    if not isinstance(value, bytes):
        raise EvalError(f"Text.FromBinary: expected binary, got {_type_name(value)}")
    return _decode(value, code_page)


def _split_delimited(
    text: str, delimiter: str, *, quoted_newlines: bool, quote_always: bool
) -> list[list[str]]:
    """Split CSV text into rows of raw fields.

    Hand-rolled rather than delegating to :mod:`csv`, which cannot express two
    things Power Query's options do: a multi-character delimiter, and
    ``QuoteStyle.None`` (a newline ends the row even inside an open quote).
    """
    rows: list[list[str]] = []
    row: list[str] = []
    field: list[str] = []
    in_quotes = False
    at_field_start = True
    width = len(delimiter)
    i = 0
    size = len(text)
    while i < size:
        char = text[i]
        if in_quotes:
            if char == '"':
                if i + 1 < size and text[i + 1] == '"':
                    field.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            if char in "\r\n" and not quoted_newlines:
                in_quotes = False  # QuoteStyle.None - fall through to row end
            else:
                field.append(char)
                i += 1
                continue
        if char == '"' and (quote_always or at_field_start):
            in_quotes = True
            at_field_start = False
            i += 1
            continue
        if width and text.startswith(delimiter, i):
            row.append("".join(field))
            field = []
            i += width
            at_field_start = True
            continue
        if char in "\r\n":
            if char == "\r" and i + 1 < size and text[i + 1] == "\n":
                i += 1
            row.append("".join(field))
            field = []
            rows.append(row)
            row = []
            i += 1
            at_field_start = True
            continue
        field.append(char)
        at_field_start = False
        i += 1
    row.append("".join(field))
    rows.append(row)
    # A file ending in a newline yields one trailing empty field, which is the
    # line terminator rather than a row of data.
    if len(rows) > 1 and rows[-1] == [""]:
        rows.pop()
    return rows


def _split_whitespace(text: str) -> list[list[str]]:
    # Delimiter "" means "split rows by consecutive whitespace" per the docs.
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if fields:
            rows.append(fields)
    return rows or [[]]


def _resolve_delimiter(value: Any) -> str:
    if value is None:
        return ","
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # "a list of characters" - any one of them separates fields. Only a
        # single distinct character is supported; more would need a real
        # alternation and guessing which wins is how columns silently shift.
        chars = {_require_str(item) for item in value}
        if len(chars) == 1:
            return chars.pop()
        raise UnsupportedError("Csv.Document: a delimiter list of several characters")
    raise EvalError(f"Csv.Document: bad delimiter of type {_type_name(value)}")


def _column_names(columns: Any, found_width: int) -> list[str]:
    if columns is None:
        return [f"Column{i + 1}" for i in range(found_width)]
    if isinstance(columns, bool):
        raise EvalError("Csv.Document: bad Columns value of type logical")
    if isinstance(columns, int):
        if columns < 0:
            raise EvalError(
                f"Csv.Document: Columns must not be negative, got {columns}"
            )
        return [f"Column{i + 1}" for i in range(columns)]
    if isinstance(columns, list):
        return [_require_str(name) for name in columns]
    from ._type import _MType

    if isinstance(columns, _MType):
        names = getattr(columns, "field_names", None)
        if names:
            return list(names)
        raise UnsupportedError("Csv.Document: Columns as a type without named fields")
    raise EvalError(f"Csv.Document: bad Columns value of type {_type_name(columns)}")


def _csv_document(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Csv.Document", args, 1, 5)
    columns: Any = args[1] if len(args) > 1 else None
    delimiter_arg: Any = args[2] if len(args) > 2 else None
    extra_values: Any = args[3] if len(args) > 3 else None
    encoding: Any = args[4] if len(args) > 4 else None
    quoted_newlines = True
    quote_always = False

    if isinstance(columns, dict):
        # Options-record form. The docs require the positional delimiter,
        # extraValues and encoding to be null when a record is used, so a call
        # that sets both is contradictory and is rejected rather than resolved
        # by a precedence rule the user cannot see.
        if (
            delimiter_arg is not None
            or extra_values is not None
            or encoding is not None
        ):
            raise EvalError(
                "Csv.Document: an options record cannot be combined with the "
                "positional delimiter/extraValues/encoding arguments"
            )
        options = columns
        columns = options.get("Columns")
        delimiter_arg = options.get("Delimiter")
        extra_values = options.get("ExtraValues")
        encoding = options.get("Encoding")
        quote_style = options.get("QuoteStyle")
        if quote_style == "QuoteStyle.None":
            quoted_newlines = False
        csv_style = options.get("CsvStyle")
        if csv_style == "CsvStyle.QuoteAlways":
            quote_always = True

    code_page = None if encoding is None else _require_int(encoding)
    text = _as_text(args[0], "Csv.Document", code_page)
    delimiter = _resolve_delimiter(delimiter_arg)

    raw_rows = (
        _split_whitespace(text)
        if delimiter == ""
        else _split_delimited(
            text,
            delimiter,
            quoted_newlines=quoted_newlines,
            quote_always=quote_always,
        )
    )
    if raw_rows == [[""]]:
        raw_rows = []

    names = _column_names(columns, len(raw_rows[0]) if raw_rows else 0)
    width = len(names)
    ignore_extra = extra_values == "ExtraValues.Ignore"

    table: list[dict[str, Any]] = []
    for index, fields in enumerate(raw_rows):
        if len(fields) > width and not ignore_extra:
            raise EvalError(
                f"Csv.Document: row {index + 1} has {len(fields)} values but "
                f"{width} column(s); pass ExtraValues.Ignore to drop the extras"
            )
        # Short rows pad with null. The docs' own prose says the additional
        # columns "will be null"; Example 3 on the same page shows "" instead.
        # The normative sentence wins, and null is what the rest of this
        # evaluator uses for absent data.
        # length here (a short row is padded on the next line, an over-long
        # one was either rejected above or is being dropped by
        # ExtraValues.Ignore), so strict=True would turn normal input into a
        # crash.
        row: dict[str, Any] = dict(zip(names, fields, strict=False))
        for name in names[len(fields) :]:
            row[name] = None
        table.append(row)
    return table


# Binary.* and Compression.* exist here because of one very common shape: the
# "Enter Data" table Power BI writes inline, which looks like
#
#   Table.FromRows(Json.Document(Binary.Decompress(
#       Binary.FromText("i45WMlTS...", BinaryEncoding.Base64),
#       Compression.Deflate)))
#
# Without these, a large share of real .pbix queries stop on the very first
# step. Compression.Deflate was verified to be *raw* deflate (negative wbits,
# no zlib header) by decompressing the payload out of a real Power BI file and
# getting well-formed JSON back - not inferred from the name.


def _binary_from_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Binary.FromText", args, 1, 2)
    text = _require_str(args[0])
    encoding = args[1] if len(args) == 2 else "BinaryEncoding.Base64"
    if encoding is None:
        encoding = "BinaryEncoding.Base64"
    try:
        if encoding == "BinaryEncoding.Base64":
            return base64.b64decode(text, validate=True)
        if encoding == "BinaryEncoding.Hex":
            return bytes.fromhex(text)
    except (binascii.Error, ValueError) as error:
        raise EvalError(f"Binary.FromText: {error}") from error
    raise UnsupportedError(
        f"Binary.FromText encoding {encoding!r} (known: BinaryEncoding.Base64, "
        "BinaryEncoding.Hex)"
    )


def _binary_to_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Binary.ToText", args, 1, 2)
    value = args[0]
    if not isinstance(value, bytes):
        raise EvalError(f"Binary.ToText: expected binary, got {_type_name(value)}")
    encoding = args[1] if len(args) == 2 else "BinaryEncoding.Base64"
    if encoding is None:
        encoding = "BinaryEncoding.Base64"
    if encoding == "BinaryEncoding.Base64":
        return base64.b64encode(value).decode("ascii")
    if encoding == "BinaryEncoding.Hex":
        return value.hex().upper()
    raise UnsupportedError(f"Binary.ToText encoding {encoding!r}")


def _binary_decompress(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Binary.Decompress", args, 2)
    value = args[0]
    if not isinstance(value, bytes):
        raise EvalError(f"Binary.Decompress: expected binary, got {_type_name(value)}")
    kind = args[1]
    try:
        if kind == "Compression.None":
            return value
        if kind == "Compression.Deflate":
            # Raw deflate - no zlib header. Verified against a real .pbix.
            return zlib.decompress(value, -zlib.MAX_WBITS)
        if kind == "Compression.GZip":
            return gzip.decompress(value)
    except (zlib.error, OSError, EOFError) as error:
        raise EvalError(f"Binary.Decompress: {error}") from error
    raise UnsupportedError(
        f"Binary.Decompress kind {kind!r} (known: Compression.None, "
        "Compression.Deflate, Compression.GZip)"
    )


def _binary_length(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Binary.Length", args, 1)
    value = args[0]
    if not isinstance(value, bytes):
        raise EvalError(f"Binary.Length: expected binary, got {_type_name(value)}")
    return len(value)


BUILTINS: dict[str, Any] = {
    "File.Contents": _file_contents,
    "Csv.Document": _csv_document,
    "Text.FromBinary": _text_from_binary,
    "Binary.FromText": _binary_from_text,
    "Binary.ToText": _binary_to_text,
    "Binary.Decompress": _binary_decompress,
    "Binary.Length": _binary_length,
    # Enum-like bare identifiers, registered as sentinels rather than numbers.
    # The numeric values of BinaryEncoding.* and Compression.* could not be
    # verified, and a wrong number is silent wrongness - but the functions
    # above only ever compare against these sentinels, so no number is needed.
    # A query passing a literal number instead raises, which is honest.
    "BinaryEncoding.Base64": "BinaryEncoding.Base64",
    "BinaryEncoding.Hex": "BinaryEncoding.Hex",
    "Compression.None": "Compression.None",
    "Compression.Deflate": "Compression.Deflate",
    "Compression.GZip": "Compression.GZip",
}
