"""Standard-library builtins that do not belong to a bigger family.

``Json.FromValue``, ``Lines.*``, ``Binary.Buffer``/``Binary.Combine``,
``Character.*``, ``Guid.From``, and the remaining ``Splitter.*``/
``Combiner.*`` pair that ``_table_shape.py`` does not already own. Grouped
here rather than invented a new family module per function - none of these
is big enough to justify its own file, and splitting one function per file
would make the registry harder to audit, not easier.

Every M semantic below is checked against learn.microsoft.com's M reference
(the worked examples on each function's own page) before being written -
see the citation in each function's docstring/comment. Where a corner of a
function's behaviour could not be pinned to a documented example (an
options-record form, an out-of-range edge), that corner raises
``UnsupportedError`` naming exactly what was refused, rather than guess.

Owned by exactly one implementer; register new names in this module's own
``BUILTINS`` dict (see ``builtins/__init__.py``).
"""

from __future__ import annotations

import base64
import datetime
import json
import math
import re
from typing import TYPE_CHECKING, Any

from ..core import _IDENTIFIER, _RESERVED
from ._connectors import _CODE_PAGES, _DEFAULT_ENCODING, _decode
from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _require_int,
    _require_list,
    _require_record,
    _require_str,
    _type_name,
)
from ._type import _MType

if TYPE_CHECKING:
    from ..evaluate import _Ctx


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def _is_function_value(value: Any) -> bool:
    """True if `value` is something ``ctx.invoke`` could call.

    Duplicated from ``_table_shape.py``'s ``_is_invocable`` rather than
    imported - that module is owned by another implementer for this task,
    and ``_list.py``'s own comment already notes the same duplication is
    the accepted pattern here (the alternative, importing across modules
    mid-edit, would couple this file to code someone else is changing
    right now). A BUILTINS-registered function is a plain Python callable;
    an ``each .../(...) => ...`` closure is a ``_Lambda`` from
    ``evaluate.py``, which has no ``__call__`` - duck-type on its
    ``__slots__`` instead.
    """
    return callable(value) or (
        hasattr(value, "params") and hasattr(value, "body") and hasattr(value, "scope")
    )


def _encoding_codec(code_page: int | None, fn_name: str) -> str:
    page = _DEFAULT_ENCODING if code_page is None else code_page
    codec = _CODE_PAGES.get(page)
    if codec is None:
        raise UnsupportedError(
            f"{fn_name}: text encoding code page {page} (known: "
            + ", ".join(str(k) for k in sorted(_CODE_PAGES))
            + ")"
        )
    return codec


# --------------------------------------------------------------------------
# Json.FromValue
# --------------------------------------------------------------------------


def _to_json_value(value: Any) -> Any:
    # Rules verified against learn.microsoft.com/en-us/powerquery-m/json-fromvalue's
    # own worked example (Text.FromBinary(Json.FromValue([A = {1, true, "3"},
    # B = #date(2012, 3, 25)])) -> '{"A":[1,true,"3"],"B":"2012-03-25"}'):
    # a record's fields become a JSON object in field order, a list/table
    # (both are `list` here - see evaluate.py's module docstring) becomes a
    # JSON array, and a date becomes ISO-8601 text with no other wrapping.
    if isinstance(value, _MType) or _is_function_value(value):
        raise EvalError("Json.FromValue: cannot represent a type or function value")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                # "#infinity, -#infinity and #nan are converted to null."
                return None
            if value.is_integer():
                # Matches _format_number's same whole-float-as-integer
                # convention in _shared.py - M has one number type, and a
                # trailing ".0" on a whole value is not something the docs'
                # own JSON example would produce.
                return int(value)
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime.timedelta):
        # duration's ISO-8601 text form (P.../PT...) could not be pinned to a
        # documented example - refusing beats guessing at the exact fields.
        raise UnsupportedError("Json.FromValue: duration values")
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_json_value(item) for key, item in value.items()}
    raise EvalError(f"Json.FromValue: cannot represent a {_type_name(value)} value")


def _json_from_value(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Json.FromValue", args, 1, 2)
    payload = _to_json_value(args[0])
    code_page = None if len(args) < 2 or args[1] is None else _require_int(args[1])
    codec = _encoding_codec(code_page, "Json.FromValue")
    # Compact, no spaces - the docs example has none between "A":[...] etc.
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.encode(codec)


# --------------------------------------------------------------------------
# Lines.*
# --------------------------------------------------------------------------


def _lines_split(text: str, quote_style: Any, include_separators: bool) -> list[str]:
    """Shared by Lines.FromText and Lines.FromBinary.

    QuoteStyle.None (the default) ends a line at every CR/LF/CRLF.
    QuoteStyle.Csv treats a double-quoted span as part of the data - a line
    break inside it does not end the line - using the same toggle-on-`"`,
    `""`-is-an-escaped-quote algorithm _table_shape.py's Splitter.* already
    uses for delimiters (RFC4180-style). Quote characters are NOT stripped
    from the output: learn.microsoft.com's own worked example for a quoted
    embedded newline shows the surrounding quotes preserved verbatim in the
    resulting line text - this function only decides where lines break, it
    never reinterprets CSV field content.

    The record form of quoteStyle (CsvStyle/Delimiter options) is refused:
    its interaction with a plain line-break scan (which has no delimiter of
    its own) was not something a docs example pinned down.
    """
    if isinstance(quote_style, dict):
        raise UnsupportedError("quoteStyle as an options record")
    style = "QuoteStyle.None" if quote_style is None else quote_style
    if style not in ("QuoteStyle.None", "QuoteStyle.Csv"):
        raise UnsupportedError(f"quoteStyle {quote_style!r}")

    lines: list[str] = []
    size = len(text)
    i = 0
    start = 0
    in_quotes = False
    while i < size:
        ch = text[i]
        if style == "QuoteStyle.Csv" and ch == '"':
            if in_quotes and i + 1 < size and text[i + 1] == '"':
                i += 2
                continue
            in_quotes = not in_quotes
            i += 1
            continue
        if not in_quotes and ch in "\r\n":
            end = i
            if ch == "\r" and i + 1 < size and text[i + 1] == "\n":
                i += 1
            i += 1
            lines.append(text[start:i] if include_separators else text[start:end])
            start = i
            continue
        i += 1
    lines.append(text[start:])
    return lines


def _lines_from_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Lines.FromText", args, 1, 3)
    text = _require_str(args[0])
    quote_style = args[1] if len(args) >= 2 else None
    include_separators = args[2] if len(args) >= 3 and args[2] is not None else False
    if not isinstance(include_separators, bool):
        raise EvalError("Lines.FromText: includeLineSeparators must be logical")
    try:
        return _lines_split(text, quote_style, include_separators)
    except UnsupportedError as error:
        raise UnsupportedError(f"Lines.FromText: {error}") from error


def _lines_from_binary(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Lines.FromBinary", args, 1, 4)
    value = args[0]
    if not isinstance(value, bytes):
        raise EvalError(f"Lines.FromBinary: expected binary, got {_type_name(value)}")
    quote_style = args[1] if len(args) >= 2 else None
    include_separators = args[2] if len(args) >= 3 and args[2] is not None else False
    if not isinstance(include_separators, bool):
        raise EvalError("Lines.FromBinary: includeLineSeparators must be logical")
    code_page = None if len(args) < 4 or args[3] is None else _require_int(args[3])
    text = _decode(value, code_page)
    try:
        return _lines_split(text, quote_style, include_separators)
    except UnsupportedError as error:
        raise UnsupportedError(f"Lines.FromBinary: {error}") from error


def _lines_to_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Lines.ToText", args, 1, 2)
    lines = [_require_str(line) for line in _require_list(args[0])]
    separator = (
        _require_str(args[1]) if len(args) == 2 and args[1] is not None else "\r\n"
    )
    # "The specified line separator is appended to each line" (docs, verbatim)
    # - after every line, including the last, not a between-lines join.
    return "".join(line + separator for line in lines)


# UTF-16 and UTF-8 have a standard byte order mark; the single-byte/legacy
# code pages in _CODE_PAGES do not, so Lines.ToBinary refuses a BOM request
# for those rather than inventing one.
_BOM_BY_CODEC: dict[str, bytes] = {
    "utf-8": b"\xef\xbb\xbf",
    "utf-16-le": b"\xff\xfe",
    "utf-16-be": b"\xfe\xff",
}


def _lines_to_binary(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Lines.ToBinary", args, 1, 4)
    lines = [_require_str(line) for line in _require_list(args[0])]
    separator = (
        _require_str(args[1]) if len(args) >= 2 and args[1] is not None else "\r\n"
    )
    code_page = None if len(args) < 3 or args[2] is None else _require_int(args[2])
    include_bom = args[3] if len(args) >= 4 and args[3] is not None else False
    if not isinstance(include_bom, bool):
        raise EvalError("Lines.ToBinary: includeByteOrderMark must be logical")
    codec = _encoding_codec(code_page, "Lines.ToBinary")
    text = "".join(line + separator for line in lines)
    data = text.encode(codec)
    if include_bom:
        bom = _BOM_BY_CODEC.get(codec)
        if bom is None:
            raise UnsupportedError(
                f"Lines.ToBinary: includeByteOrderMark for encoding {codec} "
                "(no standard byte order mark)"
            )
        data = bom + data
    return data


# --------------------------------------------------------------------------
# Binary.Buffer / Binary.Combine
# --------------------------------------------------------------------------


def _binary_buffer(args: list[Any], ctx: _Ctx) -> Any:
    # Real Power Query buffers to force a stable, single-evaluation byte
    # order out of a lazy source. This evaluator has no laziness to force -
    # every value is already fully materialised - so the only contract left
    # to honour is the signature: a (nullable) binary in, the same one out.
    _arity("Binary.Buffer", args, 1)
    value = args[0]
    if value is None:
        return None
    if not isinstance(value, bytes):
        raise EvalError(f"Binary.Buffer: expected binary, got {_type_name(value)}")
    return value


def _binary_combine(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Binary.Combine", args, 1)
    parts = _require_list(args[0])
    out = bytearray()
    for index, part in enumerate(parts):
        if not isinstance(part, bytes):
            raise EvalError(
                f"Binary.Combine: item {index} is not binary, got {_type_name(part)}"
            )
        out.extend(part)
    return bytes(out)


# --------------------------------------------------------------------------
# Character.FromNumber / Character.ToNumber
# --------------------------------------------------------------------------


def _character_from_number(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Character.FromNumber", args, 1)
    value = args[0]
    if value is None:
        return None
    code_point = _require_int(value)
    try:
        return chr(code_point)
    except (ValueError, OverflowError) as error:
        raise EvalError(
            f"Character.FromNumber: {code_point} is not a valid Unicode code point"
        ) from error


def _character_to_number(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Character.ToNumber", args, 1)
    value = args[0]
    if value is None:
        return None
    text = _require_str(value)
    # M's docs describe the result as "the character or surrogate pair" -
    # that split exists because .NET strings are UTF-16 code units. Python
    # strings are already a sequence of code points (a value outside the
    # BMP is one Python character, never two), so the equivalent single-
    # character requirement here is len(text) == 1, not 1 or 2.
    if len(text) != 1:
        raise EvalError("Character.ToNumber: expected a single character")
    return ord(text)


# --------------------------------------------------------------------------
# Guid.From
# --------------------------------------------------------------------------

# The four accepted shapes from learn.microsoft.com/en-us/powerquery-m/guid-from's
# own examples: 32 contiguous hex digits, the same grouped 8-4-4-4-12 by
# hyphens, and that grouped form wrapped in either {} or () - exactly
# .NET's Guid "N", "D", "B", and "P" format specifiers. Nothing else (a
# urn:uuid: prefix, "X" hex-array form) is in the docs, so nothing else is
# accepted - a permissive parser here would accept input real Guid.From
# rejects, which is as wrong as rejecting input it accepts.
_GUID_PLAIN = re.compile(r"^[0-9A-Fa-f]{32}$")
_GUID_HYPHENATED = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def _guid_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Guid.From", args, 1)
    value = args[0]
    if value is None:
        return None
    text = _require_str(value)
    body = text
    if len(body) >= 2 and (
        (body[0] == "{" and body[-1] == "}") or (body[0] == "(" and body[-1] == ")")
    ):
        body = body[1:-1]
    if _GUID_PLAIN.match(body):
        hex_digits = body
    elif _GUID_HYPHENATED.match(body):
        hex_digits = body.replace("-", "")
    else:
        raise EvalError(f"Guid.From: not a recognized Guid format: {text!r}")
    hex_digits = hex_digits.lower()
    return (
        f"{hex_digits[0:8]}-{hex_digits[8:12]}-{hex_digits[12:16]}-"
        f"{hex_digits[16:20]}-{hex_digits[20:32]}"
    )


# --------------------------------------------------------------------------
# Expression.Identifier / Expression.Constant / Expression.Evaluate
# --------------------------------------------------------------------------


def _expression_identifier(args: list[Any], ctx: _Ctx) -> Any:
    # Expression.Identifier(name) as text - the M SOURCE representation of
    # an identifier. Verified against both docs examples: a bare-syntax
    # name round-trips unchanged ("MyIdentifier" -> "MyIdentifier"), while
    # anything that is not valid bare-identifier syntax gets M's
    # #"..."-quoted form, doubling any embedded quote
    # ("My Identifier" -> `#"My Identifier"`). `_IDENTIFIER`/`_RESERVED`
    # are the SAME regex/keyword-set `pqtools.core.rename` already uses to
    # answer this exact question for the rename feature - reused rather
    # than re-typed, so the two can't silently drift apart.
    _arity("Expression.Identifier", args, 1)
    name = _require_str(args[0])
    if _IDENTIFIER.fullmatch(name) and name.lower() not in _RESERVED:
        return name
    return '#"' + name.replace('"', '""') + '"'


def _expression_constant(args: list[Any], ctx: _Ctx) -> Any:
    # Expression.Constant(value) as text - the M SOURCE representation of a
    # constant. Implemented for exactly the shapes the docs' own worked
    # examples prove (number, text, date); every other shape (datetime/
    # datetimezone/time/duration/binary/list/record/type/function) has no
    # worked example pinning its exact literal syntax (padding? fractional-
    # second digits? offset format?), so it is refused rather than guessed.
    _arity("Expression.Constant", args, 1)
    value = args[0]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            if math.isnan(value):
                return "#nan"
            if math.isinf(value):
                return "-#infinity" if value < 0 else "#infinity"
        # _format_number is not reused here: it renders NaN/Infinity as the
        # WORDS "NaN"/"Infinity" for display purposes elsewhere in this
        # package, which are not valid M source - only the finite-number
        # path (verified against Example 1: `Expression.Constant(123)` ->
        # `"123"`) is shared with it implicitly by producing the same
        # plain-digit text.
        return str(int(value)) if float(value).is_integer() else str(value)
    if isinstance(value, str):
        return '"' + value.replace('"', '""') + '"'
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        # Verified against Example 2: #date(2035, 01, 02) -> "#date(2035, 1, 2)"
        # - unpadded decimal, no leading zeros.
        return f"#date({value.year}, {value.month}, {value.day})"
    raise UnsupportedError(
        f"Expression.Constant for a {_type_name(value)} value (only null, "
        "logical, number, text and date are covered by a worked docs "
        "example; the exact literal syntax for anything else was not "
        "verified)"
    )


def _expression_evaluate(args: list[Any], ctx: _Ctx) -> Any:
    # Expression.Evaluate(document, optional environment) as any.
    #
    # SECURITY: `document` is arbitrary M source, often authored by someone
    # other than the caller (a workbook someone emailed you). The only
    # thing that keeps this from being a capability-widening hole is that
    # the nested evaluation reuses the CALLER's own `ctx.io` IOPolicy
    # object unchanged - the exact same deny-by-default gate a
    # `Web.Contents(...)` call already has to pass in this same query. It
    # cannot be relaxed from inside `document`: IOPolicy is immutable
    # (frozen dataclass) and is never itself an M-reachable value, so no
    # amount of `environment` shadowing can hand `document` a more
    # permissive policy than the query that invoked it already had.
    # `tests/test_values_0_10.py::test_expression_evaluate_still_blocks_web_contents`
    # proves this holds for a real Web.Contents call inside `document`.
    #
    # The nested call goes through the exact same pinned-parser + AST-walk
    # path as the outer query (evaluate.py's own module docstring: no
    # eval/exec/dynamic-import anywhere), so `document` gains no more
    # machine access than any other M text this evaluator already runs.
    #
    # `max_steps` is bounded to what remains of the CALLER's own step
    # budget rather than a fresh full budget, so an evaluate-of-evaluate
    # chain cannot multiply its ceiling arbitrarily - though, since each
    # nested call gets its own independent counter, several SIBLING calls
    # can each spend up to that remaining amount; this is a best-effort
    # bound, not an exactly-shared counter.
    _arity("Expression.Evaluate", args, 1, 2)
    document = _require_str(args[0])
    environment = args[1] if len(args) == 2 else None
    bindings = {} if environment is None else _require_record(environment)
    remaining = ctx.budget.remaining
    # Deferred (function-local) import: builtins/__init__.py is imported BY
    # evaluate.py, so a MODULE-LEVEL import here would be circular. By the
    # time this function is ever called, evaluate.py has already finished
    # importing - evaluate() is how every entry point reaches BUILTINS in
    # the first place - so the deferred import is safe at call time.
    from ..evaluate import evaluate as _evaluate_document

    return _evaluate_document(
        document, bindings=bindings, max_steps=remaining, io=ctx.io
    )


# --------------------------------------------------------------------------
# Splitter.SplitTextByLengths
# --------------------------------------------------------------------------


def _splitter_split_text_by_lengths(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Splitter.SplitTextByLengths", args, 1, 2)
    lengths = [_require_int(n) for n in _require_list(args[0])]
    for n in lengths:
        if n < 0:
            raise EvalError(
                "Splitter.SplitTextByLengths: a length must not be negative"
            )
    start_at_end = args[1] if len(args) == 2 and args[1] is not None else False
    if not isinstance(start_at_end, bool):
        raise EvalError("Splitter.SplitTextByLengths: startAtEnd must be logical")

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByLengths (applied)", inner_args, 1)
        text = _require_str(inner_args[0])
        # startAtEnd mirrors _table_shape.py's Splitter.SplitTextByPositions:
        # reverse the text, cut forward by the same lengths, then reverse
        # each piece and the piece order - verified against the docs example
        # (Splitter.SplitTextByLengths({5, 2}, true)("RedmondWA98052") ->
        # {"WA", "98052"}), which this reproduces exactly. Any text left
        # over past the last length (or short of it) is handled by ordinary
        # Python slicing - it drops silently past the end and truncates
        # gracefully short, the same leniency the docs example's exact-fit
        # input never had to exercise either way.
        source = text[::-1] if start_at_end else text
        pieces = []
        cursor = 0
        for n in lengths:
            pieces.append(source[cursor : cursor + n])
            cursor += n
        if start_at_end:
            pieces = [piece[::-1] for piece in reversed(pieces)]
        return pieces

    return _split


def _splitter_split_by_nothing(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Splitter.SplitByNothing", args, 0)

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitByNothing (applied)", inner_args, 1)
        return [_require_str(inner_args[0])]

    return _split


def _splitter_split_text_by_repeated_lengths(args: list[Any], ctx: _Ctx) -> Any:
    # Splitter.SplitTextByRepeatedLengths(length, optional startAtEnd) -
    # Splitter.SplitTextByLengths with one length applied over and over
    # until the input runs out (the final chunk may be shorter). Verified
    # against both docs examples: "12345678" split by 3 -> {"123","456",
    # "78"}; the same input reversed by startAtEnd -> {"87","654","321"},
    # reproduced exactly by the same reverse/chunk/un-reverse trick already
    # used above for SplitTextByLengths.
    _arity("Splitter.SplitTextByRepeatedLengths", args, 1, 2)
    length = _require_int(args[0])
    if length <= 0:
        raise EvalError("Splitter.SplitTextByRepeatedLengths: length must be positive")
    start_at_end = args[1] if len(args) == 2 and args[1] is not None else False
    if not isinstance(start_at_end, bool):
        raise EvalError(
            "Splitter.SplitTextByRepeatedLengths: startAtEnd must be logical"
        )

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByRepeatedLengths (applied)", inner_args, 1)
        text = _require_str(inner_args[0])
        source = text[::-1] if start_at_end else text
        pieces = [source[i : i + length] for i in range(0, len(source), length)]
        if start_at_end:
            pieces = [piece[::-1] for piece in reversed(pieces)]
        return pieces

    return _split


def _parse_ranges(raw: Any, fn_name: str) -> list[tuple[int, int | None]]:
    """``{offset, length}`` pairs shared by SplitTextByRanges/CombineTextByRanges.

    A null length means "everything else" for both functions (docs,
    verbatim, on each page).
    """
    ranges: list[tuple[int, int | None]] = []
    for item in _require_list(raw):
        pair = _require_list(item)
        if len(pair) != 2:
            raise EvalError(f"{fn_name}: each range must be {{offset, length}}")
        offset = _require_int(pair[0])
        length = None if pair[1] is None else _require_int(pair[1])
        if offset < 0 or (length is not None and length < 0):
            raise EvalError(f"{fn_name}: offset/length must not be negative")
        ranges.append((offset, length))
    return ranges


def _splitter_split_text_by_ranges(args: list[Any], ctx: _Ctx) -> Any:
    # Splitter.SplitTextByRanges(ranges, optional startAtEnd) - each range
    # is an independent {offset, length} slice (ranges MAY overlap, per the
    # docs' own Example 1). Verified against all three docs examples,
    # including startAtEnd (Example 2), which applies the SAME reverse/
    # slice/un-reverse trick as SplitTextByLengths above - traced by hand
    # against "RedmondWA?98052" and confirmed to reproduce {"WA","98052"}
    # exactly.
    _arity("Splitter.SplitTextByRanges", args, 1, 2)
    ranges = _parse_ranges(args[0], "Splitter.SplitTextByRanges")
    start_at_end = args[1] if len(args) == 2 and args[1] is not None else False
    if not isinstance(start_at_end, bool):
        raise EvalError("Splitter.SplitTextByRanges: startAtEnd must be logical")

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByRanges (applied)", inner_args, 1)
        text = _require_str(inner_args[0])
        source = text[::-1] if start_at_end else text
        pieces = [
            source[offset:] if length is None else source[offset : offset + length]
            for offset, length in ranges
        ]
        if start_at_end:
            pieces = [piece[::-1] for piece in reversed(pieces)]
        return pieces

    return _split


def _split_on_whitespace(text: str, quote_style: str) -> list[str]:
    if quote_style == "QuoteStyle.None":
        return text.split()
    # QuoteStyle.Csv: the same toggle-on-unescaped-`"`, `""`-is-an-escaped-
    # quote algorithm this file's own `_lines_split` already uses - a
    # quoted span is not split even if it contains whitespace.
    tokens: list[str] = []
    current: list[str] = []
    in_quotes = False
    i, size = 0, len(text)
    while i < size:
        ch = text[i]
        if ch == '"':
            if in_quotes and i + 1 < size and text[i + 1] == '"':
                current.append('"')
                i += 2
                continue
            in_quotes = not in_quotes
            i += 1
            continue
        if not in_quotes and ch.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def _splitter_split_text_by_whitespace(args: list[Any], ctx: _Ctx) -> Any:
    # Verified against the docs' own example: QuoteStyle.None over
    # "a b#(tab)c" (a literal tab between "b" and "c") -> {"a","b","c"}.
    _arity("Splitter.SplitTextByWhitespace", args, 0, 1)
    style = _resolve_quote_style(
        args[0] if len(args) == 1 else None, "Splitter.SplitTextByWhitespace"
    )

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByWhitespace (applied)", inner_args, 1)
        return _split_on_whitespace(_require_str(inner_args[0]), style)

    return _split


def _splitter_split_text_by_any_delimiter(args: list[Any], ctx: _Ctx) -> Any:
    # Splitter.SplitTextByAnyDelimiter(delimiters, optional quoteStyle,
    # optional startAtEnd) - like _table_shape.py's SplitTextByEachDelimiter
    # but every position may match ANY of the given delimiters (not one per
    # gap in sequence). Verified against both docs examples by hand,
    # including startAtEnd, which applies the reverse/scan/un-reverse trick
    # used throughout this file - traced character-by-character against
    # "a,\"b;c,d" and confirmed to reproduce {"a,b","c","d"} exactly.
    # Longer delimiters are tried before shorter ones at each position so a
    # delimiter that is a prefix of another can't shadow it.
    _arity("Splitter.SplitTextByAnyDelimiter", args, 1, 3)
    delimiters = sorted(
        (_require_str(d) for d in _require_list(args[0])), key=len, reverse=True
    )
    if not delimiters or any(d == "" for d in delimiters):
        raise EvalError(
            "Splitter.SplitTextByAnyDelimiter: delimiters must be non-empty text"
        )
    quote_style = _resolve_quote_style(
        args[1] if len(args) >= 2 else None, "Splitter.SplitTextByAnyDelimiter"
    )
    start_at_end = args[2] if len(args) == 3 and args[2] is not None else False
    if not isinstance(start_at_end, bool):
        raise EvalError("Splitter.SplitTextByAnyDelimiter: startAtEnd must be logical")

    def _scan(text: str) -> list[str]:
        fields: list[str] = []
        current: list[str] = []
        in_quotes = False
        i, size = 0, len(text)
        while i < size:
            ch = text[i]
            if quote_style == "QuoteStyle.Csv" and ch == '"':
                if in_quotes and i + 1 < size and text[i + 1] == '"':
                    current.append('"')
                    i += 2
                    continue
                in_quotes = not in_quotes
                i += 1
                continue
            if not in_quotes:
                matched = next((d for d in delimiters if text.startswith(d, i)), None)
                if matched is not None:
                    fields.append("".join(current))
                    current = []
                    i += len(matched)
                    continue
            current.append(ch)
            i += 1
        fields.append("".join(current))
        return fields

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByAnyDelimiter (applied)", inner_args, 1)
        text = _require_str(inner_args[0])
        source = text[::-1] if start_at_end else text
        pieces = _scan(source)
        if start_at_end:
            pieces = [piece[::-1] for piece in reversed(pieces)]
        return pieces

    return _split


# --------------------------------------------------------------------------
# Combiner.*
# --------------------------------------------------------------------------


def _csv_quote_field(value: str, delimiters: list[str]) -> str:
    # Write-side complement of _table_shape.py's _find_unquoted/
    # _unquote_csv_field: those treat every raw `"` as quote-toggling and
    # only see inside-quotes text as safe from the delimiter, so a value
    # containing a raw delimiter OR a raw quote character has to be quoted
    # (with its own quotes doubled) here or the paired Splitter could not
    # read it back correctly. Confirmed directly against the "delimiter
    # inside a field gets quoted" docs example for CombineTextByDelimiter.
    if '"' in value or any(
        delimiter and delimiter in value for delimiter in delimiters
    ):
        return '"' + value.replace('"', '""') + '"'
    return value


def _resolve_quote_style(quote_style: Any, fn_name: str) -> str:
    style = "QuoteStyle.None" if quote_style is None else quote_style
    if style not in ("QuoteStyle.None", "QuoteStyle.Csv"):
        raise UnsupportedError(f"{fn_name}: quoteStyle {quote_style!r}")
    return style


def _combiner_combine_text_by_delimiter(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Combiner.CombineTextByDelimiter", args, 1, 2)
    delimiter = _require_str(args[0])
    if delimiter == "":
        raise EvalError("Combiner.CombineTextByDelimiter: delimiter must not be empty")
    quote_style = _resolve_quote_style(
        args[1] if len(args) == 2 else None, "Combiner.CombineTextByDelimiter"
    )

    def _combine(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Combiner.CombineTextByDelimiter (applied)", inner_args, 1)
        values = [_require_str(v) for v in _require_list(inner_args[0])]
        if quote_style == "QuoteStyle.Csv":
            values = [_csv_quote_field(v, [delimiter]) for v in values]
        return delimiter.join(values)

    return _combine


def _combiner_combine_text_by_each_delimiter(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Combiner.CombineTextByEachDelimiter", args, 1, 2)
    delimiters = [_require_str(d) for d in _require_list(args[0])]
    for d in delimiters:
        if d == "":
            raise EvalError(
                "Combiner.CombineTextByEachDelimiter: delimiters must not be empty"
            )
    quote_style = _resolve_quote_style(
        args[1] if len(args) == 2 else None, "Combiner.CombineTextByEachDelimiter"
    )

    def _combine(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Combiner.CombineTextByEachDelimiter (applied)", inner_args, 1)
        values = [_require_str(v) for v in _require_list(inner_args[0])]
        gaps = len(values) - 1
        if gaps > len(delimiters):
            raise EvalError(
                "Combiner.CombineTextByEachDelimiter: "
                f"{gaps} delimiter(s) needed between {len(values)} value(s), "
                f"only {len(delimiters)} given"
            )
        if quote_style == "QuoteStyle.Csv":
            values = [_csv_quote_field(v, delimiters) for v in values]
        if not values:
            return ""
        result = values[0]
        for i in range(1, len(values)):
            result += delimiters[i - 1] + values[i]
        return result

    return _combine


def _combiner_combine_text_by_positions(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Combiner.CombineTextByPositions", args, 1, 2)
    positions = [_require_int(p) for p in _require_list(args[0])]
    for p in positions:
        if p < 0:
            raise EvalError(
                "Combiner.CombineTextByPositions: a position must not be negative"
            )
    template = args[1] if len(args) == 2 and args[1] is not None else None
    if template is not None:
        template = _require_str(template)

    def _combine(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Combiner.CombineTextByPositions (applied)", inner_args, 1)
        values = [_require_str(v) for v in _require_list(inner_args[0])]
        if len(values) != len(positions):
            raise EvalError(
                "Combiner.CombineTextByPositions: expected "
                f"{len(positions)} value(s), got {len(values)}"
            )
        needed = max(
            (p + len(v) for p, v in zip(positions, values, strict=True)), default=0
        )
        if template is None:
            # Verified against the docs example (positions {0, 5, 10} over
            # {"abc","def","ghi"} -> "abc  def  ghi"): the untouched gaps
            # between placed values come back as plain spaces.
            buffer = [" "] * needed
        else:
            if needed > len(template):
                raise EvalError(
                    "Combiner.CombineTextByPositions: template is too short "
                    "for the combined output"
                )
            buffer = list(template)
        for p, v in zip(positions, values, strict=True):
            buffer[p : p + len(v)] = v
        return "".join(buffer)

    return _combine


def _combiner_combine_text_by_lengths(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Combiner.CombineTextByLengths", args, 1, 2)
    lengths = [_require_int(n) for n in _require_list(args[0])]
    for n in lengths:
        if n < 0:
            raise EvalError(
                "Combiner.CombineTextByLengths: a length must not be negative"
            )
    template = args[1] if len(args) == 2 and args[1] is not None else None
    if template is not None:
        template = _require_str(template)

    def _combine(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Combiner.CombineTextByLengths (applied)", inner_args, 1)
        values = [_require_str(v) for v in _require_list(inner_args[0])]
        if len(values) != len(lengths):
            raise EvalError(
                "Combiner.CombineTextByLengths: expected "
                f"{len(lengths)} value(s), got {len(values)}"
            )
        # Each value contributes its first `length` characters, in sequence
        # - verified against the docs example ({1,2,3} over {"aaa","bbb",
        # "ccc"} -> "a"+"bb"+"ccc" = "abbccc"). No gaps exist between
        # lengths (unlike CombineTextByPositions), so no default-spaces
        # buffer is needed: the written text alone is the no-template
        # result, and only a supplied template can extend past it.
        written = "".join(v[:n] for v, n in zip(values, lengths, strict=True))
        if template is None:
            return written
        if len(written) > len(template):
            raise EvalError(
                "Combiner.CombineTextByLengths: template is too short for "
                "the combined output"
            )
        # Verified against docs Example 2: {1,2,3}/"*********" over
        # {"aaa","bbb","ccc"} -> "abbccc***" - the template's own tail
        # past what was written survives untouched.
        return written + template[len(written) :]

    return _combine


def _combiner_combine_text_by_ranges(args: list[Any], ctx: _Ctx) -> Any:
    # Combiner.CombineTextByRanges(ranges, optional template) - each range
    # is {position, length}; a null length writes the ENTIRE value at that
    # position (unlike CombineTextByLengths, where every length is
    # explicit). Verified against the docs' own example: ranges
    # {{0,1},{3,2},{6,null}} over {"abc","def","ghijkl"} -> "a  de ghijkl"
    # - traced by hand (only "a" of "abc", only "de" of "def", and all of
    # "ghijkl" get written; the untouched gaps default to spaces, the same
    # convention CombineTextByPositions already uses).
    _arity("Combiner.CombineTextByRanges", args, 1, 2)
    ranges = _parse_ranges(args[0], "Combiner.CombineTextByRanges")
    template = args[1] if len(args) == 2 and args[1] is not None else None
    if template is not None:
        template = _require_str(template)

    def _combine(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Combiner.CombineTextByRanges (applied)", inner_args, 1)
        values = [_require_str(v) for v in _require_list(inner_args[0])]
        if len(values) != len(ranges):
            raise EvalError(
                "Combiner.CombineTextByRanges: expected "
                f"{len(ranges)} value(s), got {len(values)}"
            )
        pieces = [
            value if length is None else value[:length]
            for value, (_, length) in zip(values, ranges, strict=True)
        ]
        needed = max(
            (
                position + len(piece)
                for (position, _), piece in zip(ranges, pieces, strict=True)
            ),
            default=0,
        )
        if template is None:
            buffer = [" "] * needed
        else:
            if needed > len(template):
                raise EvalError(
                    "Combiner.CombineTextByRanges: template is too short "
                    "for the combined output"
                )
            buffer = list(template)
        for (position, _), piece in zip(ranges, pieces, strict=True):
            buffer[position : position + len(piece)] = piece
        return "".join(buffer)

    return _combine


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
#
# NOT registered here despite being asked for in this task's brief:
# Splitter.SplitTextByDelimiter, Splitter.SplitTextByEachDelimiter,
# Splitter.SplitTextByPositions, Replacer.ReplaceText, Replacer.ReplaceValue
# - _table_shape.py already implements and registers all five (confirmed by
# grep before writing a line here); registering them again would be the
# exact duplicate-name RuntimeError builtins/__init__.py exists to catch.
BUILTINS: dict[str, Any] = {
    "Json.FromValue": _json_from_value,
    "Lines.FromText": _lines_from_text,
    "Lines.FromBinary": _lines_from_binary,
    "Lines.ToText": _lines_to_text,
    "Lines.ToBinary": _lines_to_binary,
    "Binary.Buffer": _binary_buffer,
    "Binary.Combine": _binary_combine,
    "Character.FromNumber": _character_from_number,
    "Character.ToNumber": _character_to_number,
    "Guid.From": _guid_from,
    "Expression.Identifier": _expression_identifier,
    "Expression.Constant": _expression_constant,
    "Expression.Evaluate": _expression_evaluate,
    "Splitter.SplitTextByLengths": _splitter_split_text_by_lengths,
    "Splitter.SplitByNothing": _splitter_split_by_nothing,
    "Splitter.SplitTextByRepeatedLengths": _splitter_split_text_by_repeated_lengths,
    "Splitter.SplitTextByRanges": _splitter_split_text_by_ranges,
    "Splitter.SplitTextByWhitespace": _splitter_split_text_by_whitespace,
    "Splitter.SplitTextByAnyDelimiter": _splitter_split_text_by_any_delimiter,
    "Combiner.CombineTextByDelimiter": _combiner_combine_text_by_delimiter,
    "Combiner.CombineTextByEachDelimiter": _combiner_combine_text_by_each_delimiter,
    "Combiner.CombineTextByPositions": _combiner_combine_text_by_positions,
    "Combiner.CombineTextByLengths": _combiner_combine_text_by_lengths,
    "Combiner.CombineTextByRanges": _combiner_combine_text_by_ranges,
}
