"""Type values (``type text``, ``Int64.Type``, ...) and the M functions that
consume them: ``Table.TransformColumnTypes``, ``Table.PromoteHeaders``,
``Table.DemoteHeaders``, and the ``Value.*``/``Type.Is`` introspection
functions - see PRD-0.5.0-builtins.md P0.

Representation
--------------
A type value is a small, frozen, hashable ``_MType`` with two fields:

- ``kind`` - the identity the rest of this module dispatches on. For a
  *primitive* type (``type text``, ``type date``, ...) this is the exact
  lowercase primitive-type keyword the parser hands back in the
  ``PrimitiveType`` node (``"text"``, ``"date"``, ...) - see
  ``evaluate.py``'s ``_eval_type_primary``, which imports
  ``_PRIMITIVE_TYPES`` from here the same way it already imports
  ``_ORDER_ENUM`` from ``_table.py``. For a *nominal number subtype*
  (``Int64.Type``, ``Currency.Type``, ``Percentage.Type``, ``Double.Type``,
  ``Single.Type``, ``Decimal.Type``, ``Byte.Type``, ``Int8.Type``,
  ``Int16.Type``, ``Int32.Type``) this is the identifier's own spelling,
  because each one converts differently even though every one of them also
  satisfies ``Type.Is(X, type number)`` (see ``_NUMBER_KINDS``). The
  "X.Type" identifiers that are pure aliases of a primitive (``Text.Type``,
  ``Number.Type``, ``Date.Type``, ``DateTime.Type``, ``Logical.Type``,
  ``Any.Type``) share the *same* ``_MType`` instance as their primitive
  spelling - they really are the same type in M, just two ways to spell
  it, so ``Value.Is``/``Type.Is`` need no alias-specific handling.
- ``display`` - how the value would print in M source (``"type text"``,
  ``"Int64.Type"``), used only in error messages.

Why not model the M type LANGUAGE in full (record types, table shapes,
nullable wrappers, list-of types)? Nothing downstream of
``TypePrimaryType`` needs it - every real query the PRD targets only ever
uses a type value as a ``Table.TransformColumnTypes`` conversion target or
an argument to ``Value.Is``/``Type.Is``, and this evaluator's whole data
model is already flattened (``evaluate.py``: "A TABLE is a
``list[dict[str, Any]]``"), so a table can't carry a declared *column*
type between rows for a fuller type-checker to consult anyway. Where a
real M type-system question genuinely can't be answered from this
representation - ``Value.Type``/``Value.Is`` on a list, record, or
function value - this module raises ``UnsupportedError`` naming the gap
rather than guessing: a bare ``list`` and an actual ``table`` are
byte-for-byte indistinguishable in this data model (``evaluate.py``'s own
docstring says as much), so there is no honest answer to give.

Every conversion in ``Table.TransformColumnTypes`` mirrors documented
Power Query behaviour it was cross-checked against, not intuition:
``Int64.Type`` (and the other whole-number subtypes) round with banker's
rounding (Python's ``round()`` on a float, same as ``Int64.From``'s
default ``RoundingMode.ToEven`` - NOT truncation, despite that being the
colloquial way to describe "loses the fractional part"), ``Currency.Type``
rounds to 4 decimal places the same way, and ``Percentage.Type`` is a
pure display facet over ``number`` with no value transformation at all.
``type number``/``Percentage.Type``/``Double.Type``/``Decimal.Type`` all
share one converter that keeps a value's parsed shape (int if the source
text parsed cleanly as a whole number, float otherwise) - this is an
implementation choice for this codebase specifically (M itself has no
separate int/float *value* representation), consistent with how the rest
of this evaluator already distinguishes Python ``int`` from ``float``
under the single M "number" umbrella (``_shared._type_name``).
"""

from __future__ import annotations

import datetime as _dt
import functools
import struct as _struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _format_number,
    _m_equal,
    _parse_numeric_literal,
    _require_int,
    _require_list,
    _require_record,
    _require_str,
    _require_table,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


# --------------------------------------------------------------------------
# The type value itself
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _MType:
    """An M ``type`` value - see the module docstring for what ``kind`` means."""

    kind: str
    display: str


_PRIMITIVE_TYPES: dict[str, _MType] = {
    name: _MType(kind=name, display=f"type {name}")
    for name in (
        "text",
        "number",
        "date",
        "datetime",
        "datetimezone",
        "time",
        "duration",
        "logical",
        "any",
        "none",
        "binary",
    )
}

_TYPE_TYPE = _MType(kind="type", display="type type")

# Every nominal type that is "a number, faceted": Value.Is/Type.Is treat
# all of these as satisfying Type.Is(X, type number), and
# Table.TransformColumnTypes routes every one of them through _to_number
# (some with extra rounding/range work layered on top - see
# _converter_for).
_INT_RANGES: dict[str, tuple[int, int]] = {
    "Byte.Type": (0, 255),
    "Int8.Type": (-128, 127),
    "Int16.Type": (-32768, 32767),
    "Int32.Type": (-2147483648, 2147483647),
    "Int64.Type": (-9223372036854775808, 9223372036854775807),
}

_NOMINAL_NUMBER_NAMES = (
    *_INT_RANGES,
    "Percentage.Type",
    "Currency.Type",
    "Double.Type",
    "Single.Type",
    "Decimal.Type",
)

_NUMBER_KINDS = frozenset({"number", *_NOMINAL_NUMBER_NAMES})

_NOMINAL_TYPES: dict[str, _MType] = {
    name: _MType(kind=name, display=name) for name in _NOMINAL_NUMBER_NAMES
}

# Pure aliases: the same _MType instance as the primitive they spell out.
_ALIAS_TYPES: dict[str, _MType] = {
    "Text.Type": _PRIMITIVE_TYPES["text"],
    "Number.Type": _PRIMITIVE_TYPES["number"],
    "Date.Type": _PRIMITIVE_TYPES["date"],
    "DateTime.Type": _PRIMITIVE_TYPES["datetime"],
    "Logical.Type": _PRIMITIVE_TYPES["logical"],
    "Any.Type": _PRIMITIVE_TYPES["any"],
}


# --------------------------------------------------------------------------
# Value <-> text/number/duration formatting shared by several conversions
# --------------------------------------------------------------------------


def _scalar_text(value: Any) -> str:
    """Text form of any non-``None`` scalar this module can represent."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return _format_number(value)
    if isinstance(value, str):
        return value
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, _dt.time):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return _format_duration(value)
    raise EvalError(f"cannot convert {_type_name(value)} to text")


def _format_duration(value: _dt.timedelta) -> str:
    negative = value < _dt.timedelta(0)
    magnitude = -value if negative else value
    days = magnitude.days
    hours, remainder = divmod(magnitude.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    if magnitude.microseconds:
        text += f".{magnitude.microseconds:06d}".rstrip("0")
    if days:
        text = f"{days}.{text}"
    return f"-{text}" if negative else text


def _parse_duration_text(text: str) -> _dt.timedelta:
    stripped = text.strip()
    negative = stripped.startswith("-")
    body = stripped[1:] if negative else stripped
    segments = body.split(":")
    if len(segments) != 3:
        raise EvalError(
            f"cannot parse duration {text!r} (expected [d.]hh:mm:ss[.ffffff])"
        )
    first, minute_text, second_text = segments
    day_text, hour_text = first.split(".", 1) if "." in first else ("0", first)
    try:
        days = int(day_text)
        hours = int(hour_text)
        minutes = int(minute_text)
        seconds = float(second_text)
    except ValueError as error:
        raise EvalError(f"cannot parse duration {text!r}") from error
    delta = _dt.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    return -delta if negative else delta


# --------------------------------------------------------------------------
# Per-target-type converters - each takes the CURRENT cell value and
# returns the converted value. ``None`` always maps to ``None``: "null
# stays null for every target type" (PRD correctness rule).
# --------------------------------------------------------------------------


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    return _scalar_text(value)


def _to_number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return _parse_numeric_literal(value.strip())
        except ValueError as error:
            raise EvalError(f"not a number: {value!r}") from error
    raise EvalError(f"cannot convert {_type_name(value)} to a number")


def _to_whole_number(value: Any, kind: str) -> int | None:
    number = _to_number(value)
    if number is None:
        return None
    rounded = round(number) if isinstance(number, float) else number
    low, high = _INT_RANGES[kind]
    if not low <= rounded <= high:
        raise EvalError(f"{rounded} is out of range for {kind} ({low}..{high})")
    return int(rounded)


def _to_currency(value: Any) -> float | None:
    number = _to_number(value)
    if number is None:
        return None
    return round(float(number), 4)


def _to_single(value: Any) -> float | None:
    number = _to_number(value)
    if number is None:
        return None
    # A genuine 32-bit float round-trip, not a documentation nicety: Single
    # is IEEE-754 single precision, so real Power Query loses exactly this
    # much precision converting into it.
    return float(_struct.unpack("f", _struct.pack("f", float(number)))[0])


def _to_date(value: Any) -> _dt.date | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value.strip())
        except ValueError as error:
            raise EvalError(
                f"cannot convert {value!r} to date (expected ISO 8601, YYYY-MM-DD)"
            ) from error
    raise EvalError(f"cannot convert {_type_name(value)} to date")


def _to_datetime(value: Any) -> _dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        # DateTime.From on a datetimezone value keeps the same wall-clock
        # digits and drops the offset, rather than converting to UTC.
        return value.replace(tzinfo=None)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value.strip())
        except ValueError as error:
            raise EvalError(
                f"cannot convert {value!r} to datetime (expected ISO 8601)"
            ) from error
        return parsed.replace(tzinfo=None)
    raise EvalError(f"cannot convert {_type_name(value)} to datetime")


def _to_datetimezone(value: Any) -> _dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            raise EvalError(
                "cannot convert a datetime with no timezone offset to datetimezone"
            )
        return value
    if isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value.strip())
        except ValueError as error:
            raise EvalError(
                f"cannot convert {value!r} to datetimezone (expected ISO "
                "8601 with a UTC offset)"
            ) from error
        if parsed.tzinfo is None:
            raise EvalError(
                f"{value!r} has no timezone offset - datetimezone requires one"
            )
        return parsed
    raise EvalError(f"cannot convert {_type_name(value)} to datetimezone")


def _to_time(value: Any) -> _dt.time | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.time()
    if isinstance(value, _dt.time):
        return value
    if isinstance(value, str):
        try:
            return _dt.time.fromisoformat(value.strip())
        except ValueError as error:
            raise EvalError(
                f"cannot convert {value!r} to time (expected HH:MM:SS)"
            ) from error
    raise EvalError(f"cannot convert {_type_name(value)} to time")


def _to_duration(value: Any) -> _dt.timedelta | None:
    if value is None:
        return None
    if isinstance(value, _dt.timedelta):
        return value
    if isinstance(value, bool):
        raise EvalError("cannot convert logical to duration")
    if isinstance(value, (int, float)):
        return _dt.timedelta(days=float(value))
    if isinstance(value, str):
        return _parse_duration_text(value)
    raise EvalError(f"cannot convert {_type_name(value)} to duration")


def _to_logical(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        raise EvalError(f"cannot convert {value!r} to logical")
    raise EvalError(f"cannot convert {_type_name(value)} to logical")


def _converter_for(type_value: _MType) -> Callable[[Any], Any]:
    kind = type_value.kind
    if kind == "any":
        return lambda value: value
    if kind == "text":
        return _to_text
    if kind == "number" or kind in ("Percentage.Type", "Double.Type", "Decimal.Type"):
        return _to_number
    if kind in _INT_RANGES:
        return functools.partial(_to_whole_number, kind=kind)
    if kind == "Currency.Type":
        return _to_currency
    if kind == "Single.Type":
        return _to_single
    if kind == "date":
        return _to_date
    if kind == "datetime":
        return _to_datetime
    if kind == "datetimezone":
        return _to_datetimezone
    if kind == "time":
        return _to_time
    if kind == "duration":
        return _to_duration
    if kind == "logical":
        return _to_logical
    # "none" and "binary" (and the never-a-column-target "type type") have
    # no faithful conversion in this data model - see the module docstring.
    raise UnsupportedError(
        f"Table.TransformColumnTypes target type: {type_value.display}"
    )


# --------------------------------------------------------------------------
# Table.TransformColumnTypes / Table.PromoteHeaders / Table.DemoteHeaders
# --------------------------------------------------------------------------


def _table_transform_column_types(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.TransformColumnTypes", args, 2, 3)
    table = _require_table(args[0])
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError(
            f"Table.TransformColumnTypes: culture-aware conversion ({args[2]!r}) "
            "- only the culture-invariant default (ISO 8601 dates/times, "
            "plain decimal numbers) is implemented"
        )
    pairs = _require_list(args[1])
    conversions: list[tuple[str, Callable[[Any], Any]]] = []
    for item in pairs:
        pair = _require_list(item)
        if len(pair) != 2:
            raise EvalError(
                'Table.TransformColumnTypes: each entry must be {"ColumnName", type}'
            )
        column = _require_str(pair[0])
        type_value = pair[1]
        if not isinstance(type_value, _MType):
            raise EvalError(
                "Table.TransformColumnTypes: expected a type value for column "
                f"{column!r}, got {_type_name(type_value)}"
            )
        conversions.append((column, _converter_for(type_value)))
    if not table:
        return []
    known_columns = table[0].keys()
    for column, _converter in conversions:
        if column not in known_columns:
            raise EvalError(f"Table.TransformColumnTypes: column not found: {column}")
    result = []
    for row in table:
        new_row = dict(row)
        for column, convert in conversions:
            if column not in new_row:
                raise EvalError(
                    f"Table.TransformColumnTypes: column not found: {column}"
                )
            new_row[column] = convert(new_row[column])
        result.append(new_row)
    return result


_PROMOTE_HEADERS_OPTIONS = frozenset({"PromoteAllScalars", "Culture"})


def _table_promote_headers(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.PromoteHeaders", args, 1, 2)
    table = _require_table(args[0])
    promote_all_scalars = False
    if len(args) == 2 and args[1] is not None:
        options = _require_record(args[1])
        unknown = set(options) - _PROMOTE_HEADERS_OPTIONS
        if unknown:
            raise UnsupportedError(f"Table.PromoteHeaders option(s): {sorted(unknown)}")
        culture = options.get("Culture")
        if culture is not None:
            raise UnsupportedError(
                f"Table.PromoteHeaders: Culture option ({culture!r})"
            )
        promote_value = options.get("PromoteAllScalars", False)
        if not isinstance(promote_value, bool):
            raise EvalError(
                "Table.PromoteHeaders: PromoteAllScalars must be a logical "
                f"value, got {_type_name(promote_value)}"
            )
        promote_all_scalars = promote_value
    if not table:
        return []
    header_row = table[0]
    column_keys = list(header_row.keys())
    raw_names = [header_row[key] for key in column_keys]
    new_names = _resolve_header_names(raw_names, promote_all_scalars)
    return [
        dict(zip(new_names, (row[key] for key in column_keys), strict=True))
        for row in table[1:]
    ]


def _resolve_header_names(raw_names: list[Any], promote_all_scalars: bool) -> list[str]:
    # "By default, only text or number values are promoted to headers";
    # PromoteAllScalars=true additionally promotes logical/date/time/
    # duration values. Anything else (null, list, record) always falls
    # back to a generic ColumnN name - it "cannot be converted to text".
    names: list[str] = []
    for index, value in enumerate(raw_names):
        fallback = f"Column{index + 1}"
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            names.append(_format_number(value))
        elif (
            isinstance(value, (bool, _dt.date, _dt.time, _dt.timedelta))
            and promote_all_scalars
        ):
            names.append(_scalar_text(value))
        else:
            names.append(fallback)
    return _dedupe_names(names)


def _dedupe_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        count = seen.get(name, 0)
        seen[name] = count + 1
        result.append(name if count == 0 else f"{name}.{count}")
    return result


def _table_demote_headers(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.DemoteHeaders", args, 1)
    table = _require_table(args[0])
    if not table:
        return []
    original_names = list(table[0].keys())
    new_names = [f"Column{i + 1}" for i in range(len(original_names))]
    header_row: dict[str, Any] = dict(zip(new_names, original_names, strict=True))
    demoted = [header_row]
    for row in table:
        demoted.append(
            dict(zip(new_names, (row[name] for name in original_names), strict=True))
        )
    return demoted


# --------------------------------------------------------------------------
# Value.Type / Value.Is / Value.Equals / Value.Compare / Type.Is
# --------------------------------------------------------------------------


def _classify(value: Any) -> str | None:
    """Coarse category for a runtime value, or ``None`` if unmodelled.

    Shared by ``Value.Type``, ``Value.Is``, and ``Value.Compare`` so all
    three agree on what counts as "the same kind of value". ``list``/
    ``dict`` (list, record, and table - indistinguishable in this data
    model) and functions all return ``None``: see the module docstring.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "logical"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, _dt.datetime):
        return "datetimezone" if value.tzinfo is not None else "datetime"
    if isinstance(value, _dt.date):
        return "date"
    if isinstance(value, _dt.time):
        return "time"
    if isinstance(value, _dt.timedelta):
        return "duration"
    if isinstance(value, _MType):
        return "type"
    return None


def _value_type(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Type", args, 1)
    category = _classify(args[0])
    if category == "null":
        return _PRIMITIVE_TYPES["none"]
    if category == "type":
        return _TYPE_TYPE
    if category is None:
        raise UnsupportedError(
            f"Value.Type for {_type_name(args[0])} values (list/record/table/"
            "function are not distinguishable in this evaluator's flat data "
            "model - see builtins/_type.py's module docstring)"
        )
    return _PRIMITIVE_TYPES[category]


def _value_is(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Is", args, 2)
    value, type_value = args[0], args[1]
    if not isinstance(type_value, _MType):
        raise EvalError(
            f"Value.Is: expected a type value, got {_type_name(type_value)}"
        )
    return _matches(value, type_value)


def _matches(value: Any, type_value: _MType) -> bool:
    kind = type_value.kind
    if kind == "any":
        return True
    if kind == "none":
        raise UnsupportedError("Value.Is against type none")
    if kind == "binary":
        return isinstance(value, (bytes, bytearray))
    if value is None:
        # M types are nullable by default, so null matches every type
        # except the (unimplemented, see above) "no values at all" none.
        return True
    if kind in _NUMBER_KINDS:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "logical":
        return isinstance(value, bool)
    if kind == "text":
        return isinstance(value, str)
    return _classify(value) == kind


def _value_equals(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Equals", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("Value.Equals with a precision/comparer argument")
    return _m_equal(args[0], args[1])


def _value_compare(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Compare", args, 2, 3)
    left, right = args[0], args[1]
    if len(args) == 3 and args[2] is not None:
        return _require_int(ctx.invoke(args[2], [left, right], ctx))
    return _default_compare(left, right)


_ORDERABLE_KINDS = frozenset(
    {"number", "text", "date", "datetime", "datetimezone", "time", "duration"}
)


def _default_compare(left: Any, right: Any) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    left_kind = _classify(left)
    right_kind = _classify(right)
    if left_kind is None or left_kind != right_kind:
        raise UnsupportedError(
            f"Value.Compare between {_type_name(left)} and {_type_name(right)} "
            "(cross-type default ordering is not implemented - pass a comparer)"
        )
    if left_kind == "logical":
        return int(bool(left)) - int(bool(right))
    if left_kind in _ORDERABLE_KINDS:
        if left < right:
            return -1
        if left > right:
            return 1
        return 0
    raise UnsupportedError(f"Value.Compare between {left_kind} values")


def _type_is(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.Is", args, 2)
    left, right = args[0], args[1]
    if not isinstance(left, _MType) or not isinstance(right, _MType):
        raise EvalError("Type.Is: expected two type values")
    if right.kind == "any":
        return True
    if left.kind == right.kind:
        return True
    if right.kind == "number" and left.kind in _NUMBER_KINDS:
        return True
    return False


# The M-visible names this module owns: the nominal/alias type identifiers
# (referenced as plain values, never invoked - see evaluate.py's
# _eval_identifier_expression, which returns whatever BUILTINS.get(name)
# is regardless of callability) plus the functions that consume type
# values. builtins/__init__.py merges every module's BUILTINS into one
# registry, so a new function is added HERE and nowhere else.
BUILTINS: dict[str, Any] = {
    **_NOMINAL_TYPES,
    **_ALIAS_TYPES,
    "Table.TransformColumnTypes": _table_transform_column_types,
    "Table.PromoteHeaders": _table_promote_headers,
    "Table.DemoteHeaders": _table_demote_headers,
    "Value.Type": _value_type,
    "Value.Is": _value_is,
    "Value.Equals": _value_equals,
    "Value.Compare": _value_compare,
    "Type.Is": _type_is,
}
