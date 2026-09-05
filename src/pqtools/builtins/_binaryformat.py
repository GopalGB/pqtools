"""``BinaryFormat.*`` builtins - a small parser-combinator DSL over ``bytes``.

Unlike every other family in this package, ``BinaryFormat.*`` functions do
not operate on M values directly. Each one BUILDS a *binary format value* -
an M function of type ``(binary as binary) as any`` that, when called, reads
a prefix of a binary and returns the parsed value. Primitives such as
``BinaryFormat.Byte`` already ARE that function (see their own Syntax line:
``BinaryFormat.Byte(binary as binary) as any`` - no separate factory call).
Compound combinators such as ``BinaryFormat.List``/``.Record``/``.Choice``/
``.Group``/``.Length``/``.Transform``/``.ByteOrder``/``.Binary`` are
factories: calling them (``BinaryFormat.List(BinaryFormat.Byte, 2)``)
returns a NEW format value built out of the ones passed in.

Representation
--------------
A compiled format is an instance of :class:`_Format`. It is callable as
``(args, ctx) -> Any`` - the exact shape ``evaluate.py``'s ``_invoke``
expects of any plain M function value, which is what lets a format returned
from ``BinaryFormat.List(...)`` be called directly from M source
(``listFormat(binaryData)``) exactly like a user-written lambda would be.

That public ``__call__`` only returns the parsed VALUE, because that is all
M's own type signature for a binary format exposes. But composing formats
needs more than that: ``BinaryFormat.Record([A = ..., B = ...])`` has to
read field A, know how many bytes it consumed, and start field B right
after - and M has no way to express "how many bytes did that format use" at
the language level. So ``_Format`` also carries an internal ``read(data,
offset, ctx, order)`` method, used ONLY by the combinators in this module,
that returns ``(value, new_offset)``. Every compound combinator here is
built by composing ``.read()`` calls; ``__call__`` is just ``.read(data, 0,
...)`` with the offset thrown away at the end.

One consequence: a "format" argument to a compound combinator must be
something this module can call ``.read()`` on - i.e. another ``_Format``,
built (however indirectly) from a ``BinaryFormat.*`` call. Passing an
arbitrary M lambda where a format is expected is refused by name
(``_as_format``) rather than guessed at, because there is no way to learn
how many bytes an arbitrary lambda "consumed" - the M language does not
expose that, and Microsoft's own worked examples never do this either.

Byte order
----------
``BinaryFormat.ByteOrder``'s own reference page states plainly: "The
default byte order is ``ByteOrder.BigEndian``." That is independently
confirmed by ``BinaryFormat.Record``'s own worked example, which reads
``0x00, 0x01`` as the UnsignedInteger16 value 1 and ``0x00, 0x00, 0x00,
0x02`` as the UnsignedInteger32 value 2 - both big-endian byte layouts -
with no ``BinaryFormat.ByteOrder`` wrapping anywhere in that example. Every
multi-byte primitive here therefore defaults to big-endian, and
``BinaryFormat.ByteOrder`` overrides that for everything nested beneath it
by threading an explicit order through every nested ``.read()`` call.

``ByteOrder.LittleEndian``/``ByteOrder.BigEndian`` (0/1) and
``BinaryOccurrence.Optional``/``.Required``/``.Repeating`` (0/1/2) are
registered here rather than in ``_enums.py``: this task's scope is this
file plus ``__init__.py``'s import list plus its own test file, and these
two enum families exist for no reason other than ``BinaryFormat.ByteOrder``
and ``BinaryFormat.Group``. Both numberings are verified against their own
``*-type`` reference pages (ByteOrder.Type, BinaryOccurrence.Type), the
same standard Microsoft uses for every other enum in this codebase (see
``_enums.py``'s own docstring). ``BinaryOccurrence.*`` is not optional to
register: Microsoft's own worked examples for ``BinaryFormat.Group`` use
those names as bare identifiers, and an unresolved identifier in a
documented example is the one failure class ``test_doc_examples.py`` never
tolerates. Registering these two families here means they are not yet
listed in ``tests/test_catalog.py``'s ``ENUM_AND_TYPE_VALUES`` allowlist -
that file is out of this task's scope to edit - so
``test_every_registered_builtin_is_a_real_power_query_name`` will flag
these 5 names until someone with access to that file adds them, citing:

- https://learn.microsoft.com/en-us/powerquery-m/byteorder-type
- https://learn.microsoft.com/en-us/powerquery-m/binaryoccurrence-type
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _require_int,
    _require_list,
    _require_record,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx

# Every multi-byte read threads one of these through - never a plain `str`,
# so mypy (and `int.from_bytes`'s own stub) reject a typo before it becomes a
# silently-wrong byte order.
_Order = Literal["little", "big"]
_DEFAULT_ORDER: _Order = "big"

_BYTE_ORDER_BY_CODE: dict[int, _Order] = {0: "little", 1: "big"}

# BinaryOccurrence.Type values (verified against its own reference page).
_OCCURRENCE_OPTIONAL = 0
_OCCURRENCE_REQUIRED = 1
_OCCURRENCE_REPEATING = 2

# TextEncoding.* code pages this evaluator already knows how to decode
# elsewhere (_connectors.py's `_CODE_PAGES`) - duplicated rather than
# imported, since this task's file-isolation rule is "import from
# `_shared.py`, nothing else", and `_connectors.py` is another agent's file
# to edit concurrently with this one. Limited to the six values `_enums.py`
# actually registers as `TextEncoding.*` names, so anything reachable by
# name here is also decodable here.
_TEXT_CODECS: dict[int, str] = {
    1200: "utf-16-le",  # TextEncoding.Utf16 / TextEncoding.Unicode
    1201: "utf-16-be",  # TextEncoding.BigEndianUnicode
    1252: "cp1252",  # TextEncoding.Windows
    20127: "ascii",  # TextEncoding.Ascii
    65001: "utf-8",  # TextEncoding.Utf8
}

# Sniffed in this order (longest first) when BinaryFormat.Text's `encoding`
# argument is omitted - "the encoding is determined from the Unicode byte
# order marks. If no byte order marks are present, then TextEncoding.Utf8
# is used" (that sentence is the only source for this behaviour; neither of
# the page's own two examples exercises it, both pass an explicit encoding).
_BOM_SNIFF: tuple[tuple[bytes, int], ...] = (
    (b"\xef\xbb\xbf", 65001),
    (b"\xff\xfe", 1200),
    (b"\xfe\xff", 1201),
)


def _read_bytes(data: bytes, offset: int, length: int, name: str) -> bytes:
    """Slice exactly `length` bytes at `offset`, or refuse by name.

    Rule: running past the end of a binary is a real, likely case (a
    truncated file, an off-by-one length prefix) - never returned as a
    shorter-than-requested slice, always a typed error naming the offset and
    length involved, per this task's own instructions.
    """
    if length < 0:
        raise EvalError(f"{name}: length must not be negative, got {length}")
    end = offset + length
    if offset < 0 or end > len(data):
        available = max(len(data) - offset, 0)
        raise EvalError(
            f"{name}: ran past the end of the binary - needs {length} byte(s) "
            f"starting at offset {offset}, but only {available} byte(s) are "
            f"available (the binary is {len(data)} byte(s) long)"
        )
    return data[offset:end]


_Reader = Callable[[bytes, int, "_Ctx", _Order], "tuple[Any, int]"]


class _Format:
    """A compiled ``BinaryFormat.*`` value. See the module docstring."""

    __slots__ = ("_read_fn", "_name")

    def __init__(self, read_fn: _Reader, name: str) -> None:
        self._read_fn = read_fn
        self._name = name

    def read(
        self, data: bytes, offset: int, ctx: _Ctx, order: _Order
    ) -> tuple[Any, int]:
        return self._read_fn(data, offset, ctx, order)

    def __call__(self, args: list[Any], ctx: _Ctx) -> Any:
        _arity(self._name, args, 1)
        if not isinstance(args[0], bytes):
            raise EvalError(f"{self._name}: expected binary, got a different type")
        value, _consumed = self._read_fn(args[0], 0, ctx, _DEFAULT_ORDER)
        return value


def _as_format(value: Any, name: str) -> _Format:
    if isinstance(value, _Format):
        return value
    raise UnsupportedError(
        f"{name}: a binary format built from something other than another "
        "BinaryFormat.* value - M gives no way to learn how many bytes an "
        "arbitrary function consumed, which every combinator here needs in "
        "order to know where the next field starts"
    )


# --------------------------------------------------------------------------
# Primitive formats - values, not factories (see module docstring: their own
# Syntax line already reads `(binary as binary) as any`, so the identifier
# itself IS the function, with no call needed to produce one).
# --------------------------------------------------------------------------


def _make_scalar(
    name: str, size: int, decode: Callable[[bytes, _Order], Any]
) -> _Format:
    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        chunk = _read_bytes(data, offset, size, name)
        return decode(chunk, order), offset + size

    return _Format(read_fn, name)


def _decode_decimal(chunk: bytes, order: _Order) -> int | float:
    """The .NET ``System.Decimal`` 16-byte layout: 4 32-bit WORDS in the
    fixed stream order (lo, mid, hi, flags) - a stable, decades-old external
    format, not something Microsoft's M docs define. That field ORDER is not
    itself a byte-order question and stays fixed either way; only the byte
    order WITHIN each 32-bit word follows `order`, exactly as it would for
    four consecutive UnsignedInteger32 reads. Neither this interaction nor
    any decimal value at all appears in Microsoft's own Syntax/About page for
    BinaryFormat.Decimal (no Example section exists on it), so this is the
    best-faith reproduction of the external .NET format the page names, not
    a Microsoft-verified worked example - flagged as the single highest-
    uncertainty piece of this module.
    """
    lo = int.from_bytes(chunk[0:4], order, signed=False)
    mid = int.from_bytes(chunk[4:8], order, signed=False)
    hi = int.from_bytes(chunk[8:12], order, signed=False)
    flags = int.from_bytes(chunk[12:16], order, signed=False)
    scale = (flags >> 16) & 0xFF
    if scale > 28:
        raise EvalError(
            f"BinaryFormat.Decimal: scale byte {scale} exceeds .NET decimal's "
            "maximum of 28 - these 16 bytes are not a well-formed decimal"
        )
    sign = -1 if (flags >> 31) & 1 else 1
    mantissa = (hi << 64) | (mid << 32) | lo
    if scale == 0:
        return sign * mantissa
    quotient: float = mantissa / (10**scale)
    return sign * quotient


def _decode_float(fmt: str) -> Callable[[bytes, _Order], float]:
    import struct

    def decode(chunk: bytes, order: _Order) -> float:
        prefix = "<" if order == "little" else ">"
        return float(struct.unpack(prefix + fmt, chunk)[0])

    return decode


_BYTE = _make_scalar("BinaryFormat.Byte", 1, lambda chunk, _order: chunk[0])
_NULL = _make_scalar("BinaryFormat.Null", 0, lambda _chunk, _order: None)
_SIGNED_16 = _make_scalar(
    "BinaryFormat.SignedInteger16", 2, lambda c, o: int.from_bytes(c, o, signed=True)
)
_SIGNED_32 = _make_scalar(
    "BinaryFormat.SignedInteger32", 4, lambda c, o: int.from_bytes(c, o, signed=True)
)
_SIGNED_64 = _make_scalar(
    "BinaryFormat.SignedInteger64", 8, lambda c, o: int.from_bytes(c, o, signed=True)
)
_UNSIGNED_16 = _make_scalar(
    "BinaryFormat.UnsignedInteger16", 2, lambda c, o: int.from_bytes(c, o, signed=False)
)
_UNSIGNED_32 = _make_scalar(
    "BinaryFormat.UnsignedInteger32", 4, lambda c, o: int.from_bytes(c, o, signed=False)
)
_UNSIGNED_64 = _make_scalar(
    "BinaryFormat.UnsignedInteger64", 8, lambda c, o: int.from_bytes(c, o, signed=False)
)
_SINGLE = _make_scalar("BinaryFormat.Single", 4, _decode_float("f"))
_DOUBLE = _make_scalar("BinaryFormat.Double", 8, _decode_float("d"))
_DECIMAL = _make_scalar("BinaryFormat.Decimal", 16, _decode_decimal)


# --------------------------------------------------------------------------
# Factory combinators - calling these BUILDS a format.
# --------------------------------------------------------------------------


def _binary_format_binary(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.Binary", args, 0, 1)
    length_spec = args[0] if args else None

    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        if length_spec is None:
            return data[offset:], len(data)
        if isinstance(length_spec, _Format):
            length, start = length_spec.read(data, offset, ctx, order)
            chunk = _read_bytes(
                data, start, _require_int(length), "BinaryFormat.Binary"
            )
            return chunk, start + len(chunk)
        length = _require_int(length_spec)
        chunk = _read_bytes(data, offset, length, "BinaryFormat.Binary")
        return chunk, offset + length

    return _Format(read_fn, "BinaryFormat.Binary")


def _binary_format_byte_order(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.ByteOrder", args, 2)
    inner = _as_format(args[0], "BinaryFormat.ByteOrder")
    code = _require_int(args[1])
    order = _BYTE_ORDER_BY_CODE.get(code)
    if order is None:
        raise EvalError(
            "BinaryFormat.ByteOrder: byteOrder must be ByteOrder.LittleEndian "
            f"(0) or ByteOrder.BigEndian (1), got {code}"
        )

    def read_fn(
        data: bytes, offset: int, ctx: _Ctx, _outer_order: _Order
    ) -> tuple[Any, int]:
        return inner.read(data, offset, ctx, order)

    return _Format(read_fn, "BinaryFormat.ByteOrder")


def _binary_format_choice(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.Choice", args, 2, 4)
    first = _as_format(args[0], "BinaryFormat.Choice")
    choose = args[1]
    # args[2] ("type") only hints that the result MAY be returned as a
    # streaming binary/list instead of a buffered one - a memory-use detail
    # this evaluator, which always buffers, has no way to act on. The page's
    # own Example 3 prints the identical value with or without it, so
    # ignoring the hint changes no output this evaluator can produce.
    combine = args[3] if len(args) == 4 else None

    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        first_value, offset2 = first.read(data, offset, ctx, order)
        second = _as_format(
            ctx.invoke(choose, [first_value], ctx),
            "BinaryFormat.Choice: chooseFunction's return value",
        )
        second_value, offset3 = second.read(data, offset2, ctx, order)
        if combine is None:
            return second_value, offset3
        return ctx.invoke(combine, [first_value, second_value], ctx), offset3

    return _Format(read_fn, "BinaryFormat.Choice")


class _GroupItem(NamedTuple):
    key: Any
    format: _Format
    occurrence: int
    default: Any
    transform: Any


def _group_items(value: Any) -> list[_GroupItem]:
    items: list[_GroupItem] = []
    for entry in _require_list(value):
        parts = _require_list(entry)
        if not 3 <= len(parts) <= 5:
            raise EvalError(
                "BinaryFormat.Group: each group item must be a list of "
                "{key, format, occurrence, optional default, optional "
                f"transform}}, got {len(parts)} value(s)"
            )
        key = parts[0]
        item_format = _as_format(parts[1], "BinaryFormat.Group: item format")
        occurrence = _require_int(parts[2])
        if occurrence not in (
            _OCCURRENCE_OPTIONAL,
            _OCCURRENCE_REQUIRED,
            _OCCURRENCE_REPEATING,
        ):
            raise EvalError(
                "BinaryFormat.Group: item occurrence must be "
                "BinaryOccurrence.Optional/.Required/.Repeating (0/1/2), "
                f"got {occurrence}"
            )
        # "The default for repeating or optional items is null, and the
        # default for repeating values is an empty list { }" is Microsoft's
        # own sentence, but taking it literally contradicts the page's own
        # Example 1 (a Repeating key that never appears prints `{}`, not
        # `null`). Read the way the example actually behaves: optional-and-
        # absent defaults to null, repeating-and-absent defaults to `{}`,
        # unless the item definition supplies its own default value.
        if len(parts) >= 4 and parts[3] is not None:
            default: Any = parts[3]
        else:
            default = [] if occurrence == _OCCURRENCE_REPEATING else None
        transform = parts[4] if len(parts) == 5 else None
        items.append(_GroupItem(key, item_format, occurrence, default, transform))
    keys = [item.key for item in items]
    duplicates = sorted({repr(k) for k in keys if keys.count(k) > 1})
    if duplicates:
        raise EvalError(
            f"BinaryFormat.Group: duplicate key value(s) in group: {duplicates}"
        )
    return items


def _binary_format_group(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.Group", args, 2, 4)
    key_format = _as_format(args[0], "BinaryFormat.Group")
    items = _group_items(args[1])
    extra = args[2] if len(args) >= 3 else None
    if len(args) == 4 and args[3] is not None:
        raise UnsupportedError(
            "BinaryFormat.Group: the lastKey parameter - Microsoft's own "
            "reference has no worked example of it and does not specify how "
            "it interacts with the end-of-data stopping condition precisely "
            "enough to reproduce faithfully here"
        )
    by_key = {item.key: index for index, item in enumerate(items)}

    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        collected: dict[int, Any] = {}
        repeated: dict[int, list[Any]] = {}
        pos = offset
        while pos < len(data):
            before = pos
            key_value, pos = key_format.read(data, pos, ctx, order)
            index = by_key.get(key_value)
            item = items[index] if index is not None else None
            # "Required or optional duplicate items are handled like
            # unexpected key values" - verified against Example 1, where key
            # 1 (Required) appears a second time at the end of the input and
            # is routed to `extra` exactly like the genuinely-unknown key 5,
            # rather than raising or overwriting the first read.
            duplicate = (
                item is not None
                and item.occurrence != _OCCURRENCE_REPEATING
                and index in collected
            )
            if item is None or duplicate:
                if extra is None:
                    raise EvalError(
                        f"BinaryFormat.Group: unexpected key {key_value!r} and "
                        "no `extra` function was given to handle it"
                    )
                extra_format = _as_format(
                    ctx.invoke(extra, [key_value], ctx),
                    "BinaryFormat.Group: extra's return value",
                )
                _discarded, pos = extra_format.read(data, pos, ctx, order)
            else:
                assert index is not None  # item came from items[index] above
                value, pos = item.format.read(data, pos, ctx, order)
                if item.occurrence == _OCCURRENCE_REPEATING:
                    repeated.setdefault(index, []).append(value)
                else:
                    collected[index] = value
            if pos == before:
                raise EvalError(
                    "BinaryFormat.Group: a full key/value cycle read zero "
                    "bytes; reading until end of data would never terminate"
                )
        result: list[Any] = []
        for index, item in enumerate(items):
            if item.occurrence == _OCCURRENCE_REPEATING:
                values = repeated.get(index, [])
                if not values:
                    result.append(item.default)
                elif item.transform is not None:
                    result.append(ctx.invoke(item.transform, [values], ctx))
                else:
                    result.append(values)
                continue
            if index in collected:
                value = collected[index]
                if item.transform is not None:
                    value = ctx.invoke(item.transform, [value], ctx)
                result.append(value)
            elif item.occurrence == _OCCURRENCE_REQUIRED:
                raise EvalError(
                    f"BinaryFormat.Group: required key {item.key!r} never appeared"
                )
            else:
                result.append(item.default)
        return result, pos

    return _Format(read_fn, "BinaryFormat.Group")


def _binary_format_length(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.Length", args, 2)
    inner = _as_format(args[0], "BinaryFormat.Length")
    length_spec = args[1]

    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        if isinstance(length_spec, _Format):
            length_value, start = length_spec.read(data, offset, ctx, order)
            length = _require_int(length_value)
        else:
            length = _require_int(length_spec)
            start = offset
        # A Length window is always fully consumed once opened, whatever the
        # inner format actually used - the same convention a fixed-size
        # length-prefixed chunk uses everywhere else (unused trailing bytes
        # are padding, not a sign to keep reading). Bounding the data the
        # inner format can SEE to exactly this window is what makes
        # Example 1's unbounded `BinaryFormat.List(BinaryFormat.Byte)` stop
        # at 2 items instead of reading the whole 3-byte input.
        end = start + length
        if end > len(data):
            raise EvalError(
                f"BinaryFormat.Length: ran past the end of the binary - "
                f"needs {length} byte(s) starting at offset {start}, but "
                f"only {max(len(data) - start, 0)} byte(s) are available "
                f"(the binary is {len(data)} byte(s) long)"
            )
        value, _inner_end = inner.read(data[:end], start, ctx, order)
        return value, end

    return _Format(read_fn, "BinaryFormat.Length")


def _binary_format_list(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.List", args, 1, 2)
    item_format = _as_format(args[0], "BinaryFormat.List")
    spec = args[1] if len(args) == 2 else None

    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        result: list[Any] = []
        pos = offset
        if spec is None:
            while pos < len(data):
                before = pos
                value, pos = item_format.read(data, pos, ctx, order)
                result.append(value)
                if pos == before:
                    raise EvalError(
                        "BinaryFormat.List: the item format read zero bytes; "
                        "reading until end of data would never terminate"
                    )
            return result, pos
        if isinstance(spec, _Format):
            count_value, pos = spec.read(data, pos, ctx, order)
            count = _require_int(count_value)
        elif isinstance(spec, bool):
            raise EvalError(
                "BinaryFormat.List: countOrCondition must not be a logical value"
            )
        elif isinstance(spec, (int, float)):
            count = _require_int(spec)
        else:
            count = None
        if count is not None:
            if count < 0:
                raise EvalError(
                    f"BinaryFormat.List: count must not be negative, got {count}"
                )
            for _ in range(count):
                value, pos = item_format.read(data, pos, ctx, order)
                result.append(value)
            return result, pos
        # A predicate: read the item, keep it (the doc's own Example 3
        # includes the item that FIRST answers false), then ask whether to
        # continue.
        while True:
            before = pos
            value, pos = item_format.read(data, pos, ctx, order)
            result.append(value)
            keep_going = ctx.invoke(spec, [value], ctx)
            if not isinstance(keep_going, bool):
                raise EvalError(
                    "BinaryFormat.List: countOrCondition function must return "
                    "a logical value"
                )
            if not keep_going:
                break
            if pos == before:
                raise EvalError(
                    "BinaryFormat.List: the item format read zero bytes and "
                    "the condition kept requesting more; this would never "
                    "terminate"
                )
        return result, pos

    return _Format(read_fn, "BinaryFormat.List")


def _binary_format_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.Record", args, 1)
    fields = _require_record(args[0])

    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        result: dict[str, Any] = {}
        pos = offset
        for field_name, field_spec in fields.items():
            # "If a field contains a value that is not a binary format
            # value, then no data is read for that field, and the field
            # value is echoed to the result" - verbatim from the page.
            if isinstance(field_spec, _Format):
                result[field_name], pos = field_spec.read(data, pos, ctx, order)
            else:
                result[field_name] = field_spec
        return result, pos

    return _Format(read_fn, "BinaryFormat.Record")


def _sniff_text_encoding(chunk: bytes) -> tuple[int, bytes]:
    for bom, code_page in _BOM_SNIFF:
        if chunk.startswith(bom):
            return code_page, chunk[len(bom) :]
    return 65001, chunk


def _binary_format_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.Text", args, 1, 2)
    length_spec = args[0]
    encoding_spec = args[1] if len(args) == 2 else None
    fixed_code_page: int | None = None
    if encoding_spec is not None:
        fixed_code_page = _require_int(encoding_spec)
        if fixed_code_page not in _TEXT_CODECS:
            raise UnsupportedError(
                f"BinaryFormat.Text: text encoding code page {fixed_code_page} "
                "(known: " + ", ".join(str(k) for k in sorted(_TEXT_CODECS)) + ")"
            )

    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        if isinstance(length_spec, _Format):
            length_value, start = length_spec.read(data, offset, ctx, order)
            length = _require_int(length_value)
        else:
            length = _require_int(length_spec)
            start = offset
        chunk = _read_bytes(data, start, length, "BinaryFormat.Text")
        end = start + length
        if fixed_code_page is not None:
            code_page, payload = fixed_code_page, chunk
        else:
            code_page, payload = _sniff_text_encoding(chunk)
        try:
            text = payload.decode(_TEXT_CODECS[code_page])
        except UnicodeDecodeError as error:
            raise EvalError(f"BinaryFormat.Text: {error}") from error
        return text, end

    return _Format(read_fn, "BinaryFormat.Text")


def _binary_format_transform(args: list[Any], ctx: _Ctx) -> Any:
    _arity("BinaryFormat.Transform", args, 2)
    inner = _as_format(args[0], "BinaryFormat.Transform")
    transform = args[1]

    def read_fn(data: bytes, offset: int, ctx: _Ctx, order: _Order) -> tuple[Any, int]:
        value, pos = inner.read(data, offset, ctx, order)
        return ctx.invoke(transform, [value], ctx), pos

    return _Format(read_fn, "BinaryFormat.Transform")


BUILTINS: dict[str, Any] = {
    "BinaryFormat.Binary": _binary_format_binary,
    "BinaryFormat.Byte": _BYTE,
    "BinaryFormat.ByteOrder": _binary_format_byte_order,
    "BinaryFormat.Choice": _binary_format_choice,
    "BinaryFormat.Decimal": _DECIMAL,
    "BinaryFormat.Double": _DOUBLE,
    "BinaryFormat.Group": _binary_format_group,
    "BinaryFormat.Length": _binary_format_length,
    "BinaryFormat.List": _binary_format_list,
    "BinaryFormat.Null": _NULL,
    "BinaryFormat.Record": _binary_format_record,
    "BinaryFormat.SignedInteger16": _SIGNED_16,
    "BinaryFormat.SignedInteger32": _SIGNED_32,
    "BinaryFormat.SignedInteger64": _SIGNED_64,
    "BinaryFormat.Single": _SINGLE,
    "BinaryFormat.Text": _binary_format_text,
    "BinaryFormat.Transform": _binary_format_transform,
    "BinaryFormat.UnsignedInteger16": _UNSIGNED_16,
    "BinaryFormat.UnsignedInteger32": _UNSIGNED_32,
    "BinaryFormat.UnsignedInteger64": _UNSIGNED_64,
    # ByteOrder.Type / BinaryOccurrence.Type - see module docstring for why
    # these two enum families are registered here instead of `_enums.py`.
    "ByteOrder.LittleEndian": 0,
    "ByteOrder.BigEndian": 1,
    "BinaryOccurrence.Optional": _OCCURRENCE_OPTIONAL,
    "BinaryOccurrence.Required": _OCCURRENCE_REQUIRED,
    "BinaryOccurrence.Repeating": _OCCURRENCE_REPEATING,
}
