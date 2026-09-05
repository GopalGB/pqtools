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
  ``_PRIMITIVE_TYPES`` from here. For a *nominal number subtype*
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

Type introspection (``Type.RecordFields``, ``Type.ForRecord``,
``Type.ForFunction``, ``Type.AddTableKey``, ``Type.Union``, ...) - PRD
0.8.x's ``Type.*``/``Value.*`` batch
--------------------------------------------------------------------------
This batch needed ``_MType`` to carry more than "kind + display + table
field names", so it now also carries (all optional, ``None``/empty when
not applicable or not known - see each field's own comment below):
``field_types``, ``field_optional`` (per-field declared type/optionality,
parallel to ``field_names``), ``is_open`` (record openness), ``keys`` and
``partition_key`` (table-type facets managed entirely by
``Type.AddTableKey``/``Type.Replace*`` - never part of the ``type table
[...]`` grammar itself), ``parameters``/``min_arity``/``return_type``
(function types), ``union_members`` (``Type.Union``), and ``facets``
(``Type.Facets``/``Type.ReplaceFacets``, generic on any kind).

The honest limit this enrichment runs into: **every one of these new
fields is fully populated for a type value built by a function in THIS
module** (``Type.ForRecord``, ``Type.ForFunction``, ``Type.Union``,
``Type.AddTableKey`` and friends), because those functions construct the
``_MType`` themselves and can fill in whatever they like. But a type value
built from actual M *syntax* - ``type table [A = text, B = number]``,
parsed by ``evaluate.py``'s ``_table_type_value`` - only ever gets
``field_names``. Verified directly against the pinned
``@microsoft/powerquery-parser`` AST (not assumed): a ``FieldSpecification``
node has a sibling ``FieldTypeSpecification`` holding the field's actual
type expression (``PrimitiveType``, a nested ``TableType``, a
``NullableType``, ...), and ``_table_type_value`` walks past it, reading
only the ``GeneralizedIdentifier``. So ``field_types``/``field_optional``
stay ``None`` for every table type this evaluator's parser produces, and
every function below that needs a *declared* column type
(``Type.TableColumn``, ``Type.TableRow``, and half of
``Type.TableSchema``) raises ``UnsupportedError`` naming exactly that gap
rather than guessing ``any`` for a column whose author wrote ``type text``.
See ``Type.TableColumn`` below for precisely what an ``evaluate.py`` fix
would need to walk (this file cannot make that change - it does not own
``evaluate.py``).

One correction to a claim that used to live on the ``field_names`` field
below: it said real Power BI output writes a field's type as ``((type
text) meta [...])``, i.e. that ``meta`` shows up *per field*. Checked
directly against the parser: that shape does not parse as a
``FieldTypeSpecification`` value at all - ``meta`` only attaches to the
*whole* ``type table [...]`` expression (``(type table [...]) meta
[...]``), never to one field inside it. The claim was wrong; a per-field
type IS reachable (it is just a plain ``PrimitiveType``/``TableType``/
``NullableType`` node), which is what makes the ``evaluate.py`` fix above
smaller than that old comment implied.
"""

from __future__ import annotations

import datetime as _dt
import functools
import struct as _struct
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ._shared import (
    _MISSING_FIELD_ERROR,
    _MISSING_FIELD_IGNORE,
    EvalError,
    UnsupportedError,
    _arity,
    _field_name_list,
    _format_number,
    _m_equal,
    _missing_field_mode,
    _numeric_quotient,
    _parse_numeric_literal,
    _require_int,
    _require_list,
    _require_number,
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
class _TableKey:
    """One entry of a table type's key list.

    Exists only because ``Type.AddTableKey``/``Type.ReplaceTableKeys`` need
    somewhere to put it - the M grammar for ``type table [...]`` has no key
    syntax at all, so a table type's keys are ALWAYS added by calling one of
    those two functions, never by parsing. That is why this is not a parser
    gap the way field types are: nothing here is ever missing, only ever
    empty (a fresh table type simply has no keys yet).
    """

    columns: tuple[str, ...]
    primary: bool


@dataclass(frozen=True, slots=True)
class _MType:
    """An M ``type`` value - see the module docstring for what ``kind`` means.

    Every field after ``display`` is optional and defaults to "not known /
    not applicable" (``None`` for anything that can be legitimately absent,
    ``()`` for a list-shaped fact whose natural empty state - no keys, no
    facets - is itself a well-defined answer rather than a missing one).
    Whether a given field is populated for a particular value depends on
    *how that value was built*, not on its ``kind`` alone - see the module
    docstring's "Type introspection" section for the split between "built by
    a function in this module" (always populated) and "built by parsing
    `type table [...]`" (names only, forever, until `evaluate.py` changes).
    """

    kind: str
    display: str
    # Field names of a `type table [...]` OR a record type built by
    # `Type.ForRecord`, in declaration order.
    field_names: tuple[str, ...] | None = None
    # Parallel to `field_names`: each field's declared type. Populated for a
    # record type built by `Type.ForRecord` (which requires it as input) and
    # for anything derived from one (`Type.TableRow`'s output, for example).
    # `None` for a table type parsed from `type table [...]` - see the
    # module docstring; this evaluator's parser never captures it there.
    field_types: tuple[_MType, ...] | None = None
    # Parallel to `field_names`: each field's `optional` flag. Same
    # availability rule as `field_types` - populated together, or not at
    # all.
    field_optional: tuple[bool, ...] | None = None
    # Record-type openness (`type [A = number, ...]` vs `type [A =
    # number]`). Only meaningful for `kind == "record"`; `None` for every
    # other kind, including "table" - a table type's field list has no open
    # variant in the M grammar (verified: `type table [A = text, ...]` does
    # not parse), so this is never a gap to fill in for tables, just an
    # inapplicable question.
    is_open: bool | None = None
    # Table-type keys, managed entirely by `Type.AddTableKey`/
    # `Type.ReplaceTableKeys` - see `_TableKey`'s own docstring for why this
    # is never a parser gap. Empty by default: a table type simply starts
    # with no keys.
    keys: tuple[_TableKey, ...] = ()
    # Table-type partition key, managed by `Type.ReplaceTablePartitionKey`.
    # `None` means "never set" (also `Type.TablePartitionKey`'s documented
    # answer for that state - "if it has one"); an explicit `()` means "set
    # to an empty key", a distinct, if unusual, state a `nullable list`
    # argument can express.
    partition_key: tuple[str, ...] | None = None
    # Function-type shape, built only by `Type.ForFunction` (`type function
    # (...) as ...` itself does not parse here - the AST's `FunctionType`
    # node is not one `_eval_type_primary` handles). `parameters` is
    # (name, type) pairs in declared order; `min_arity` is `Type.
    # ForFunction`'s own `min` argument.
    parameters: tuple[tuple[str, _MType], ...] | None = None
    min_arity: int | None = None
    return_type: _MType | None = None
    # `Type.Union`'s member types, in the order given. `None` for every
    # non-union kind.
    union_members: tuple[_MType, ...] | None = None
    # `Type.Facets`/`Type.ReplaceFacets` - a generic, order-preserving
    # key/value annotation on ANY type value (the docs place no kind
    # restriction on either function). Empty by default: "no facets set" is
    # itself the correct answer for a type nothing has ever called
    # `Type.ReplaceFacets` on, matching `Value.Metadata`'s own "no metadata"
    # `[]` default elsewhere in this file. Stored as pairs rather than a
    # `dict` so `_MType` stays hashable (a `dict` is not); a facet value
    # that is itself an M list/record would still make this one instance
    # unhashable if something tried to hash it, exactly as that list/record
    # value already is on its own - not a new failure mode this field
    # introduces.
    facets: tuple[tuple[str, Any], ...] = ()
    # Set by `type nullable T`, which evaluate.py can now build. Before that
    # it could not, and Type.IsNullable/Type.NonNullable were written around
    # that fact - see their comment, which this field made obsolete.
    is_nullable: bool = False


# The M specification's `primitive-type` production, complete and verbatim:
#
#   any anynonnull binary date datetime datetimezone duration function list
#   logical none null number record table text time type
#
# Eleven of the eighteen were registered. The other seven simply did not
# evaluate, so `type list` - which BinaryFormat.Choice's own documented
# example passes - failed with "type value: type list" as though it were a
# misspelling rather than a keyword of the language.
#
# Registering a name is not the same as being able to TEST against it.
# `_classify` answers `null` and `type` outright; list, record, table and
# function are deliberately indistinguishable in this evaluator's data model
# (a table IS a list of records here), so `Value.Is` against those still
# raises the explicit refusal it always did. That is the honest split: the
# type VALUE exists because the language says it does, and the one question
# this evaluator cannot answer about it still says so.
_PRIMITIVE_TYPES: dict[str, _MType] = {
    name: _MType(kind=name, display=f"type {name}")
    for name in (
        "any",
        "anynonnull",
        "binary",
        "date",
        "datetime",
        "datetimezone",
        "duration",
        "function",
        "list",
        "logical",
        "none",
        "null",
        "number",
        "record",
        "table",
        "text",
        "time",
        "type",
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
    # "If a record is specified for culture, it can contain the following
    # fields: Culture ... MissingField". Only the bare-text form was
    # recognised, so the documented record form was reported as an
    # unimplemented CULTURE even when its Culture field was absent.
    culture: Any = args[2] if len(args) == 3 else None
    missing_field = None
    if isinstance(culture, dict):
        options = dict(culture)
        culture = options.pop("Culture", None)
        missing_field = options.pop("MissingField", None)
        if options:
            raise UnsupportedError(
                f"Table.TransformColumnTypes: option(s) {sorted(options)}"
            )
    if culture is not None:
        raise UnsupportedError(
            f"Table.TransformColumnTypes: culture-aware conversion ({culture!r}) "
            "- only the culture-invariant default (ISO 8601 dates/times, "
            "plain decimal numbers) is implemented"
        )
    mode = _missing_field_mode(missing_field)
    # `{"a", type text}` - ONE transformation, not a list of two. The page's
    # own Example 1 writes it that way ("The format for a single
    # transformation is { column name, type value }"), and it was rejected
    # with "expected a list, got text". Exactly the shape, and exactly the
    # ambiguity, that `_shared._sort_criteria` already documents for
    # Table.Sort: a column name is text, so an entry whose first element is
    # text is one transformation rather than a list of them.
    raw = _require_list(args[1])
    pairs = [raw] if raw and isinstance(raw[0], str) else raw
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
    if mode == _MISSING_FIELD_IGNORE:
        conversions = [c for c in conversions if c[0] in known_columns]
    elif mode == _MISSING_FIELD_ERROR:
        for column, _converter in conversions:
            if column not in known_columns:
                raise EvalError(
                    f"Table.TransformColumnTypes: column not found: {column}"
                )
    result = []
    for index, row in enumerate(table):
        new_row = dict(row)
        for column, convert in conversions:
            if column not in new_row:
                # Only reachable under MissingField.UseNull: the column is
                # added, empty, and the conversion runs on null.
                new_row[column] = None
            try:
                new_row[column] = convert(new_row[column])
            except EvalError as error:
                # Power Query marks the individual cell as an error and keeps
                # loading; this evaluator has no cell-error value, so it stops.
                # Stopping without saying where is the unhelpful part: a real
                # CSV has one bad cell in ten thousand rows, and "not a number:
                # \'\'" alone gives the user nowhere to look.
                raise EvalError(
                    f"Table.TransformColumnTypes: row {index + 1}, column "
                    f"{column!r}: {error}. Power Query would mark this one "
                    "cell as an error and continue; replace the value first, "
                    'e.g. Table.ReplaceValue(t, "", null, Replacer.'
                    f"ReplaceValue, {{{column!r}}})"
                ) from error
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
    """Does `value` conform to `type_value`? (Value.Is / Type.Is.)

    The kind rules below MUST agree with `evaluate._conforms`, which answers
    the same question for the `is` operator. They are two implementations of
    one concept and they have already been caught disagreeing: registering
    the M spec's full primitive-type set made `Value.Is({1, 2}, type list)`
    return FALSE here while `{1, 2} is list` returned true there, because
    this function fell through to `_classify`, which reports None for a list.
    A false answer is worse than the refusal it replaced.

    Two divergences remain, deliberately not changed here because each would
    alter many existing answers and neither is what the registration broke:
    `type none` (this raises, `_conforms` returns false) and null
    conformance (this treats every type as nullable, `_conforms` requires an
    explicit `nullable`). Both want the M specification open alongside them.
    """
    kind = type_value.kind
    if kind == "any":
        return True
    if kind == "none":
        raise UnsupportedError("Value.Is against type none")
    if kind == "anynonnull":
        # The one primitive whose answer for null is false, so it has to be
        # decided before the "null matches everything" shortcut below.
        return value is not None
    if kind == "binary":
        return isinstance(value, (bytes, bytearray))
    if kind == "union":
        # The one unambiguous meaning "matches a union type" can have: the
        # value satisfies at least one member. No worked example on Type.
        # Union's own (nearly empty) doc page exercises this, but there is
        # no second sensible reading of what a union TYPE means for value
        # membership - this is the textbook definition, not a guess.
        assert type_value.union_members is not None
        return any(_matches(value, member) for member in type_value.union_members)
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
    if kind == "record":
        return isinstance(value, dict)
    if kind == "function":
        return callable(value) or hasattr(value, "params")
    if kind in ("list", "table"):
        if not isinstance(value, list):
            return False
        if value and all(isinstance(item, dict) for item in value):
            # A table here IS a list of records, so the two answers are the
            # same object. Same refusal `evaluate._conforms` raises for the
            # `is` operator, and for the same reason.
            raise UnsupportedError(
                "Value.Is list/table against a list of records: this "
                "evaluator models an M table as exactly that, so the two "
                "are indistinguishable here"
            )
        return kind == "list"
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
    return _types_conform(left, right)


def _types_conform(left: _MType, right: _MType) -> bool:
    """Whether type ``left`` conforms to (satisfies) type ``right``.

    Split out of ``_type_is`` so the two new ``Type.Union`` branches below
    can recurse into the same check without re-running the arity/isinstance
    validation on every member. For every kind that existed before
    ``Type.Union``, this is byte-for-byte the original ``Type.Is`` body -
    the union branches are new arms that only ever fire for a kind that
    could not exist until this batch added ``Type.Union``, so no previously
    passing comparison changes answer.
    """
    if right.kind == "union":
        # T conforms to Union(A, B) iff T conforms to A or to B - the
        # standard "matches any branch" reading, same one `_matches` uses.
        assert right.union_members is not None
        return any(_types_conform(left, member) for member in right.union_members)
    if left.kind == "union":
        # Union(A, B) conforms to T only if BOTH A and B do - the standard
        # subtyping rule for a union on the source side: whichever branch
        # actually shows up must still satisfy T.
        assert left.union_members is not None
        return all(_types_conform(member, right) for member in left.union_members)
    if right.kind == "any":
        return True
    if left.kind == right.kind:
        return True
    if right.kind == "number" and left.kind in _NUMBER_KINDS:
        return True
    return False


# --------------------------------------------------------------------------
# Type.IsNullable / Type.NonNullable
#
# These two were written around a fact that has since stopped being true.
# `type nullable text` used to raise at parse time - evaluate.py modelled
# only `PrimitiveType` and `TableType` - so both functions could reason that
# the nullable wrapper was simply unreachable and answer a constant. It is
# reachable now (`_type_value` handles `NullableType`), and answering the
# old constant would make `Type.IsNullable(type nullable text)` return
# false: a wrong answer, not a missing one.
#
# The M spec's "Nullable types" section still makes `type nullable T`
# abstract - "no value can be directly of abstract type" - which is about
# what values can INHABIT the type, not about whether the type value itself
# can be written down and inspected. These read the flag.
# --------------------------------------------------------------------------


def _type_is_nullable(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.IsNullable", args, 1)
    type_value = args[0]
    if not isinstance(type_value, _MType):
        raise EvalError(
            f"Type.IsNullable: expected a type value, got {_type_name(type_value)}"
        )
    if type_value.kind == "any":
        # The spec states `type nullable any` is equivalent to `any`, but
        # gives no worked example of what Type.IsNullable itself returns
        # for `any` specifically, and this evaluator's _MType carries no
        # nullable flag to check instead of guessing - so this one shape is
        # refused rather than assumed either way.
        raise UnsupportedError(
            "Type.IsNullable(type any): ambiguous from the M language spec "
            "(`type nullable any` is stated equivalent to `any`, but no "
            "worked example gives IsNullable's verdict for `any` itself) "
            "and this evaluator's type values carry no nullable flag to "
            "check instead of guessing"
        )
    # The docs' own Example 1 is the non-nullable case
    # (`Type.IsNullable(type number)` -> `false`), which falls out of the
    # flag being unset on everything the `nullable` keyword did not wrap.
    return type_value.is_nullable


def _type_non_nullable(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.NonNullable", args, 1)
    type_value = args[0]
    if not isinstance(type_value, _MType):
        raise EvalError(
            f"Type.NonNullable: expected a type value, got {_type_name(type_value)}"
        )
    if type_value.kind == "any":
        # Spec equivalence: `Type.NonNullable(type any)` == `type
        # anynonnull` - a type this evaluator does not model at all (it is
        # not in _PRIMITIVE_TYPES). Returning `type any` unchanged would be
        # silently wrong, not merely incomplete.
        raise UnsupportedError(
            "Type.NonNullable(type any): the M language spec states this "
            "is equivalent to `type anynonnull`, a type this evaluator "
            "does not model"
        )
    # Strip the flag. On a type that never had it this is the no-op the
    # spec's idempotence rule requires: `Type.NonNullable(Type.NonNullable(
    # type T)) == Type.NonNullable(type T)`.
    if not type_value.is_nullable:
        return type_value
    display = type_value.display.replace("type nullable ", "type ", 1)
    return replace(type_value, display=display, is_nullable=False)


# --------------------------------------------------------------------------
# Value.Metadata / Value.RemoveMetadata / Value.ReplaceMetadata /
# Value.Optimize / Value.NativeQuery
#
# `meta` (the M operator that attaches metadata to a value) is unimplemented
# here - evaluate.py's `_SIMPLE_UNSUPPORTED` table refuses `MetadataExpression`
# outright - and Value.ReplaceMetadata (the only OTHER way to attach
# metadata) is refused below. So no value this evaluator ever produces can
# carry attached metadata, which makes Value.Metadata/RemoveMetadata fully,
# honestly answerable rather than approximated: see each function for the
# citation.
# --------------------------------------------------------------------------


def _value_metadata(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Metadata", args, 1)
    # The docs' own Value.RemoveMetadata Example 1 proves the answer for a
    # value with no metadata attached: stripping metadata and then asking
    # for it back returns the empty record `[]`. Every value here is in
    # that state (see the section note above), always.
    return {}


def _value_remove_metadata(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.RemoveMetadata", args, 1, 2)
    if len(args) == 2 and args[1] is not None:
        _field_name_list(args[1])  # validated; nothing to remove either way
    # Every value already carries no metadata (see the section note above),
    # so "strip metadata" is a no-op that returns the value unchanged.
    return args[0]


def _value_replace_metadata(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.ReplaceMetadata", args, 2)
    raise UnsupportedError(
        "Value.ReplaceMetadata: attaching metadata that a later "
        "Value.Metadata call could read back requires a value wrapper this "
        "evaluator's flat data model does not have - plain int/text/list/"
        "record values carry no side channel for it, so returning the "
        "value unchanged would make every later Value.Metadata call lie "
        "about it"
    )


def _value_optimize(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Optimize", args, 1)
    # "When used within Value.Expression, ... indicates the optimized
    # expression should be returned. Otherwise, value is passed through
    # with no effect." (docs, verbatim). Value.Expression is not
    # implemented here (this evaluator tracks no AST/expression provenance
    # for a runtime value), so the "within Value.Expression" trigger can
    # never fire - the "otherwise" branch is the only behaviour this
    # evaluator can ever exercise, and it is a plain pass-through.
    return args[0]


def _value_native_query(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.NativeQuery", args, 2, 4)
    raise UnsupportedError(
        "Value.NativeQuery: runs a target-specific query (e.g. T-SQL) "
        "against a live connection to `target`. pqtools evaluates M "
        "locally and opens no such connection"
    )


# --------------------------------------------------------------------------
# Value.As / Value.NullableEquals / Value.Add / Value.Subtract /
# Value.Multiply / Value.Divide
# --------------------------------------------------------------------------


def _value_as(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.As", args, 2)
    value, type_value = args[0], args[1]
    if not isinstance(type_value, _MType):
        raise EvalError(
            f"Value.As: expected a type value, got {_type_name(type_value)}"
        )
    if not _matches(value, type_value):
        raise EvalError(
            f"Value.As: the value of type {_type_name(value)} is not "
            f"compatible with {type_value.display}"
        )
    return value


def _value_nullable_equals(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.NullableEquals", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("Value.NullableEquals with a precision argument")
    if args[0] is None or args[1] is None:
        return None
    return _m_equal(args[0], args[1])


def _make_value_arithmetic(
    name: str, apply: Callable[[int | float, int | float], int | float]
) -> Callable[[list[Any], _Ctx], Any]:
    # Value.Add/Subtract/Multiply/Divide all accept an optional `precision`
    # (Precision.Double by default, per each function's own docs). pqtools
    # has one numeric representation (Python int/float - see
    # _shared._type_name) and does not model Precision.Double vs
    # Precision.Decimal separately, so a non-null precision is refused
    # rather than silently ignored. This mirrors the `+`/`-`/`*`/`/`
    # operators' own numeric-only restriction (evaluate.py's
    # `_eval_arithmetic`) exactly, so Value.Add etc. never accept an
    # operand the bare operator would refuse either.
    def _fn(args: list[Any], ctx: _Ctx) -> Any:
        _arity(name, args, 2, 3)
        if len(args) == 3 and args[2] is not None:
            raise UnsupportedError(f"{name} with a precision argument")
        left = _require_number(args[0])
        right = _require_number(args[1])
        return apply(left, right)

    return _fn


def _add(left: int | float, right: int | float) -> int | float:
    return left + right


def _subtract(left: int | float, right: int | float) -> int | float:
    return left - right


def _multiply(left: int | float, right: int | float) -> int | float:
    return left * right


def _divide(left: int | float, right: int | float) -> int | float:
    # Documented as "the result of dividing value1 by value2" - the same
    # operation as `/`, so it must give the same answer, IEEE zeros included.
    # Number.IntegerDivide and Number.Mod still raise: integer division by
    # zero has no IEEE result to return.
    return _numeric_quotient(left, right)


_value_add = _make_value_arithmetic("Value.Add", _add)
_value_subtract = _make_value_arithmetic("Value.Subtract", _subtract)
_value_multiply = _make_value_arithmetic("Value.Multiply", _multiply)
_value_divide = _make_value_arithmetic("Value.Divide", _divide)


# --------------------------------------------------------------------------
# Shared helpers for the record/function/table-key/union machinery below.
# --------------------------------------------------------------------------


def _require_type_kind(value: Any, kind: str, what: str, name: str) -> _MType:
    """``value`` must be an ``_MType`` of exactly ``kind`` (e.g. "table")."""
    if not isinstance(value, _MType):
        raise EvalError(f"{name}: expected a type value, got {_type_name(value)}")
    if value.kind != kind:
        raise EvalError(f"{name}: expected a {what}, got {value.display}")
    return value


def _bare_type_name(type_value: _MType) -> str:
    """A type's name the way M writes it after ``as`` - bare, no ``type ``.

    ``display`` already IS this for a nominal type (``"Int64.Type"``); for a
    primitive it is ``"type text"``, so only that case needs the prefix
    stripped. Used only inside the composite ``display`` strings built
    below - see ``_MType.display``'s own note that it is for error messages
    only, never compared for equality.
    """
    return type_value.display.removeprefix("type ")


def _record_type_display(
    names: list[str], types: list[_MType], optional: list[bool], is_open: bool
) -> str:
    parts = [
        f"{'optional ' if opt else ''}{name} = {_bare_type_name(t)}"
        for name, t, opt in zip(names, types, optional, strict=True)
    ]
    if is_open:
        parts.append("...")
    return "type [" + ", ".join(parts) + "]"


def _record_field_specs(record_type: _MType) -> list[tuple[str, _MType, bool]]:
    """(name, type, optional) triples for a record-kind ``_MType``.

    Every record-kind value in this evaluator is built by ``Type.
    ForRecord`` (directly, or via ``Type.TableRow`` reusing the same three
    tuples), and both always fill ``field_names``/``field_types``/
    ``field_optional`` together - so for ``kind == "record"`` they are never
    ``None``. The asserts below are for mypy, not a runtime possibility this
    function is guessing past.
    """
    assert record_type.field_names is not None
    assert record_type.field_types is not None
    assert record_type.field_optional is not None
    return list(
        zip(
            record_type.field_names,
            record_type.field_types,
            record_type.field_optional,
            strict=True,
        )
    )


# --------------------------------------------------------------------------
# Type.ForRecord / Type.RecordFields / Type.ClosedRecord / Type.OpenRecord /
# Type.IsOpenRecord
#
# `type [A = number, ...]` - a bare record type literal - does not parse in
# this evaluator at all (`_eval_type_primary` only handles `PrimitiveType`
# and `TableType`; a `RecordType` node hits its `UnsupportedError` branch,
# which is exactly what `test_compound_type_shapes_are_unsupported_not_
# guessed` pins). So every doc-page example for the five functions in this
# section - all of which write that literal directly - fails to parse
# before reaching any of the code below, through no fault of this module's
# own logic. What follows is fully exercised instead by composing through
# `Type.ForRecord`, the one legitimate way this evaluator can ever produce
# a record-kind `_MType` (see `Type.TableRow` further down for the other).
# --------------------------------------------------------------------------


def _type_for_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.ForRecord", args, 2)
    fields = _require_record(args[0])
    is_open = args[1]
    if not isinstance(is_open, bool):
        raise EvalError(
            f"Type.ForRecord: open must be a logical value, got {_type_name(is_open)}"
        )
    names: list[str] = []
    types: list[_MType] = []
    optionals: list[bool] = []
    for field_name, spec in fields.items():
        spec_record = _require_record(spec)
        if "Type" not in spec_record or "Optional" not in spec_record:
            raise EvalError(
                "Type.ForRecord: each field must be a [Type = type, Optional "
                f"= logical] record - field {field_name!r} has {sorted(spec_record)}"
            )
        field_type = spec_record["Type"]
        if not isinstance(field_type, _MType):
            raise EvalError(
                f"Type.ForRecord: field {field_name!r}'s Type must be a type "
                f"value, got {_type_name(field_type)}"
            )
        optional = spec_record["Optional"]
        if not isinstance(optional, bool):
            raise EvalError(
                f"Type.ForRecord: field {field_name!r}'s Optional must be a "
                f"logical value, got {_type_name(optional)}"
            )
        names.append(field_name)
        types.append(field_type)
        optionals.append(optional)
    return _MType(
        kind="record",
        display=_record_type_display(names, types, optionals, is_open),
        field_names=tuple(names),
        field_types=tuple(types),
        field_optional=tuple(optionals),
        is_open=is_open,
    )


def _type_record_fields(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.RecordFields", args, 1)
    record_type = _require_type_kind(
        args[0], "record", "record type", "Type.RecordFields"
    )
    return {
        name: {"Type": field_type, "Optional": optional}
        for name, field_type, optional in _record_field_specs(record_type)
    }


def _type_closed_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.ClosedRecord", args, 1)
    record_type = _require_type_kind(
        args[0], "record", "record type", "Type.ClosedRecord"
    )
    if record_type.is_open is False:
        return record_type
    fields = _record_field_specs(record_type)
    display = _record_type_display(
        [name for name, _, _ in fields],
        [field_type for _, field_type, _ in fields],
        [optional for _, _, optional in fields],
        False,
    )
    return replace(record_type, is_open=False, display=display)


def _type_open_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.OpenRecord", args, 1)
    record_type = _require_type_kind(
        args[0], "record", "record type", "Type.OpenRecord"
    )
    if record_type.is_open is True:
        return record_type
    fields = _record_field_specs(record_type)
    display = _record_type_display(
        [name for name, _, _ in fields],
        [field_type for _, field_type, _ in fields],
        [optional for _, _, optional in fields],
        True,
    )
    return replace(record_type, is_open=True, display=display)


def _type_is_open_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.IsOpenRecord", args, 1)
    record_type = _require_type_kind(
        args[0], "record", "record type", "Type.IsOpenRecord"
    )
    assert record_type.is_open is not None
    return record_type.is_open


# --------------------------------------------------------------------------
# Type.ForFunction / Type.FunctionParameters / Type.FunctionRequiredParameters
# / Type.FunctionReturn
#
# `type function (x as number) as any` also does not parse as a type value
# here (verified against the AST: it is a `FunctionType` node, and
# `_eval_type_primary` only handles `PrimitiveType`/`TableType`), so - same
# story as the record functions above - every doc example for the three
# introspection functions below fails to parse before reaching this module.
# `Type.ForFunction` itself has no such dependency: its inputs are ordinary
# M values (a record, a number), not a type-expression literal, so it is
# fully exercised by its own doc example, and the other three are fully
# exercised by composing through it.
# --------------------------------------------------------------------------


def _function_type_display(
    parameters: list[tuple[str, _MType]], return_type: _MType
) -> str:
    params = ", ".join(f"{name} as {_bare_type_name(t)}" for name, t in parameters)
    return f"type function ({params}) as {_bare_type_name(return_type)}"


def _type_for_function(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.ForFunction", args, 2)
    signature = _require_record(args[0])
    if "ReturnType" not in signature or "Parameters" not in signature:
        raise EvalError(
            "Type.ForFunction: signature must be a [ReturnType = type, "
            "Parameters = record] record"
        )
    return_type = signature["ReturnType"]
    if not isinstance(return_type, _MType):
        raise EvalError(
            "Type.ForFunction: ReturnType must be a type value, got "
            f"{_type_name(return_type)}"
        )
    parameters_record = _require_record(signature["Parameters"])
    parameters: list[tuple[str, _MType]] = []
    for param_name, param_type in parameters_record.items():
        if not isinstance(param_type, _MType):
            raise EvalError(
                f"Type.ForFunction: parameter {param_name!r} must be a type "
                f"value, got {_type_name(param_type)}"
            )
        parameters.append((param_name, param_type))
    min_arity = _require_int(args[1])
    if not 0 <= min_arity <= len(parameters):
        raise EvalError(
            f"Type.ForFunction: min ({min_arity}) must be between 0 and the "
            f"number of parameters ({len(parameters)})"
        )
    return _MType(
        kind="function",
        display=_function_type_display(parameters, return_type),
        parameters=tuple(parameters),
        min_arity=min_arity,
        return_type=return_type,
    )


def _type_function_parameters(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.FunctionParameters", args, 1)
    function_type = _require_type_kind(
        args[0], "function", "function type", "Type.FunctionParameters"
    )
    assert function_type.parameters is not None
    return dict(function_type.parameters)


def _type_function_required_parameters(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.FunctionRequiredParameters", args, 1)
    function_type = _require_type_kind(
        args[0], "function", "function type", "Type.FunctionRequiredParameters"
    )
    assert function_type.min_arity is not None
    return function_type.min_arity


def _type_function_return(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.FunctionReturn", args, 1)
    function_type = _require_type_kind(
        args[0], "function", "function type", "Type.FunctionReturn"
    )
    assert function_type.return_type is not None
    return function_type.return_type


# --------------------------------------------------------------------------
# Type.AddTableKey / Type.TableKeys / Type.ReplaceTableKeys /
# Type.TablePartitionKey / Type.ReplaceTablePartitionKey
#
# Keys and the partition key are never part of the `type table [...]`
# grammar (M has no key syntax there) - they exist ONLY because one of
# these five functions set them, so unlike field types these are never a
# parser gap. Every worked doc example round-trips through this module's
# own functions (`Type.AddTableKey` then `Type.TableKeys`, etc.), so all
# five run end to end.
# --------------------------------------------------------------------------


def _parse_table_key(name: str, spec: Any) -> _TableKey:
    record = _require_record(spec)
    if "Columns" not in record or "Primary" not in record:
        raise EvalError(
            f"{name}: each key must be a [Columns = {{...}}, Primary = "
            f"...] record, got {sorted(record)}"
        )
    columns = tuple(_require_str(c) for c in _require_list(record["Columns"]))
    primary = record["Primary"]
    if not isinstance(primary, bool):
        raise EvalError(
            f"{name}: Primary must be a logical value, got {_type_name(primary)}"
        )
    return _TableKey(columns=columns, primary=primary)


def _validate_table_keys(
    name: str, keys: list[_TableKey], field_names: tuple[str, ...] | None
) -> None:
    primaries = [key for key in keys if key.primary]
    if len(primaries) > 1:
        raise EvalError(f"{name}: a table type may have at most one primary key")
    if field_names is None:
        return
    known = set(field_names)
    for key in keys:
        missing = [column for column in key.columns if column not in known]
        if missing:
            raise EvalError(f"{name}: key column(s) not found: {missing}")


def _type_add_table_key(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.AddTableKey", args, 3)
    table_type = _require_type_kind(args[0], "table", "table type", "Type.AddTableKey")
    columns = tuple(_require_str(c) for c in _require_list(args[1]))
    is_primary = args[2]
    if not isinstance(is_primary, bool):
        raise EvalError(
            "Type.AddTableKey: isPrimary must be a logical value, got "
            f"{_type_name(is_primary)}"
        )
    keys = [*table_type.keys, _TableKey(columns=columns, primary=is_primary)]
    _validate_table_keys("Type.AddTableKey", keys, table_type.field_names)
    return replace(table_type, keys=tuple(keys))


def _type_table_keys(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.TableKeys", args, 1)
    table_type = _require_type_kind(args[0], "table", "table type", "Type.TableKeys")
    return [
        {"Columns": list(key.columns), "Primary": key.primary}
        for key in table_type.keys
    ]


def _type_replace_table_keys(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.ReplaceTableKeys", args, 2)
    table_type = _require_type_kind(
        args[0], "table", "table type", "Type.ReplaceTableKeys"
    )
    keys = [
        _parse_table_key("Type.ReplaceTableKeys", item)
        for item in _require_list(args[1])
    ]
    _validate_table_keys("Type.ReplaceTableKeys", keys, table_type.field_names)
    return replace(table_type, keys=tuple(keys))


def _type_table_partition_key(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.TablePartitionKey", args, 1)
    table_type = _require_type_kind(
        args[0], "table", "table type", "Type.TablePartitionKey"
    )
    if table_type.partition_key is None:
        return None
    return list(table_type.partition_key)


def _type_replace_table_partition_key(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.ReplaceTablePartitionKey", args, 2)
    table_type = _require_type_kind(
        args[0], "table", "table type", "Type.ReplaceTablePartitionKey"
    )
    partition_key = args[1]
    if partition_key is None:
        return replace(table_type, partition_key=None)
    # The page gives no worked example to pin the list's CONTENTS, only its
    # shape ("nullable list"). Read as a list of column names: the only
    # other list-shaped fact this evaluator's table types carry is `Type.
    # TableKeys`' `Columns`, and a partition key is the same kind of fact
    # (which column(s) partition the table) - not validated against
    # `field_names` the way key columns are, because `Type.ReplaceTableKeys`
    # documents that validation explicitly and this page does not.
    columns = tuple(_require_str(c) for c in _require_list(partition_key))
    return replace(table_type, partition_key=columns)


# --------------------------------------------------------------------------
# Type.TableColumn / Type.TableRow / Type.TableSchema
#
# All three need a table type's DECLARED column types, which - see the
# module docstring - this evaluator's parser never captures for a `type
# table [...]` literal (only field NAMES). Type.TableColumn/TableRow each
# return exactly one type value with no honest partial answer available, so
# both refuse outright until that gap closes. Type.TableSchema returns a
# TABLE, which - like `Table.Schema` itself elsewhere in this codebase -
# can fill in what is known (Name, Position) and leave the rest null rather
# than guessed, so it never refuses.
# --------------------------------------------------------------------------


def _type_table_column(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.TableColumn", args, 2)
    table_type = _require_type_kind(args[0], "table", "table type", "Type.TableColumn")
    column = _require_str(args[1])
    if table_type.field_names is None or column not in table_type.field_names:
        raise EvalError(f"Type.TableColumn: column not found: {column}")
    if table_type.field_types is None:
        raise UnsupportedError(
            "Type.TableColumn: this evaluator's parser does not capture "
            "field types for `type table [...]` literals - evaluate.py's "
            "_table_type_value reads only each FieldSpecification's "
            "GeneralizedIdentifier (the name), never its sibling "
            "FieldTypeSpecification (the declared type) - so column "
            f"{column!r} is known to exist but its type cannot be recovered"
        )
    index = table_type.field_names.index(column)
    return table_type.field_types[index]


def _type_table_row(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.TableRow", args, 1)
    table_type = _require_type_kind(args[0], "table", "table type", "Type.TableRow")
    if table_type.field_types is None or table_type.field_optional is None:
        raise UnsupportedError(
            "Type.TableRow: the row type is a record type built from every "
            "column's declared type, and this evaluator's parser does not "
            "capture column types for `type table [...]` literals (see "
            "Type.TableColumn's error for exactly what evaluate.py would "
            "need to change) - there is no honest record type to build"
        )
    field_names = table_type.field_names or ()
    return _MType(
        kind="record",
        display=_record_type_display(
            list(field_names),
            list(table_type.field_types),
            list(table_type.field_optional),
            False,
        ),
        field_names=field_names,
        field_types=table_type.field_types,
        field_optional=table_type.field_optional,
        is_open=False,
    )


def _schema_type_name(type_value: _MType) -> str:
    if type_value.kind in _NUMBER_KINDS:
        return "number"
    if type_value.kind in _PRIMITIVE_TYPES:
        return type_value.kind
    return _bare_type_name(type_value)


def _type_table_schema(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.TableSchema", args, 1)
    table_type = _require_type_kind(args[0], "table", "table type", "Type.TableSchema")
    field_names = table_type.field_names or ()
    field_types = table_type.field_types
    rows = []
    for position, name in enumerate(field_names):
        type_name = (
            _schema_type_name(field_types[position])
            if field_types is not None
            else None
        )
        rows.append(
            {
                "Name": name,
                "Position": position,
                "TypeName": type_name,
                "Kind": type_name,
                # This codebase's own Table.Schema (_table_shape.py) reports
                # IsNullable from the DATA ("does any row hold a null in
                # this column") - a question with no answer here, since a
                # bare type has no rows to inspect. Left null rather than
                # reinterpreted as the type system's own, different,
                # nullability flag (Type.IsNullable), which the page does
                # not say this column is either.
                "IsNullable": None,
                "NumericPrecision": None,
                "NumericScale": None,
                "NativeTypeName": None,
                "Description": None,
            }
        )
    return rows


# --------------------------------------------------------------------------
# Type.Facets / Type.ReplaceFacets
# --------------------------------------------------------------------------


def _type_facets(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.Facets", args, 1)
    if not isinstance(args[0], _MType):
        raise EvalError(
            f"Type.Facets: expected a type value, got {_type_name(args[0])}"
        )
    return dict(args[0].facets)


def _type_replace_facets(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.ReplaceFacets", args, 2)
    if not isinstance(args[0], _MType):
        raise EvalError(
            f"Type.ReplaceFacets: expected a type value, got {_type_name(args[0])}"
        )
    facets = _require_record(args[1])
    return replace(args[0], facets=tuple(facets.items()))


# --------------------------------------------------------------------------
# Type.Union
# --------------------------------------------------------------------------


def _type_union(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.Union", args, 1)
    members: list[_MType] = []
    for member in _require_list(args[0]):
        if not isinstance(member, _MType):
            raise EvalError(
                f"Type.Union: expected a type value, got {_type_name(member)}"
            )
        members.append(member)
    display = "type (" + " | ".join(_bare_type_name(member) for member in members) + ")"
    return _MType(kind="union", display=display, union_members=tuple(members))


# --------------------------------------------------------------------------
# Type.ListItem
#
# A list-item type can only ever come from a `type {T}` list-type value.
# That shape does not parse here at all (`_eval_type_primary` refuses any
# type_node kind other than PrimitiveType/TableType - `type {number}` is a
# `ListType` node, which is exactly what `test_compound_type_shapes_are_
# unsupported_not_guessed` already pins as UnsupportedError), and no
# function in this module builds a "list" kind `_MType` any other way (M
# itself has no `Type.ForList`). So this function can never legitimately
# receive a list-type argument - unlike the table/record/function gaps
# above, this is not a parser change away from working, because there is no
# runtime representation of a list-of type in this evaluator's data model
# to hand back even if the argument parsed (see the module's very first
# docstring paragraph on why list-of types are not modelled at all).
# --------------------------------------------------------------------------


def _type_list_item(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Type.ListItem", args, 1)
    if not isinstance(args[0], _MType):
        raise EvalError(
            f"Type.ListItem: expected a type value, got {_type_name(args[0])}"
        )
    raise UnsupportedError(
        "Type.ListItem: a list-item type can only come from a `type {T}` "
        "list-type value, which this evaluator's parser refuses to "
        "construct at all (see the module docstring - list-of types are "
        "not modelled), and no function here builds one any other way"
    )


# --------------------------------------------------------------------------
# Value.Alternates / Value.Expression / Value.ReplaceType /
# Value.VersionIdentity / Value.Versions
# --------------------------------------------------------------------------


def _value_alternates(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Alternates", args, 1)
    raise UnsupportedError(
        "Value.Alternates: the docs state this is meaningful only inside a "
        "query-plan expression obtained through Value.Expression(Value."
        "Optimize(...)), and 'not intended for other uses' - this evaluator "
        "implements neither query-plan introspection nor a real optimizer "
        "(Value.Optimize here is a documented pass-through, see above), so "
        "that expression can never exist for this to alternate within"
    )


def _value_expression(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Expression", args, 1)
    raise UnsupportedError(
        "Value.Expression: returns an AST for the value's expression, but "
        "this evaluator tracks no expression/AST provenance on a runtime "
        "value (the same limitation Value.Optimize's own comment documents "
        "above) - there is no AST here to hand back for any value"
    )


def _value_replace_type(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.ReplaceType", args, 2)
    if not isinstance(args[1], _MType):
        raise EvalError(
            f"Value.ReplaceType: expected a type value, got {_type_name(args[1])}"
        )
    raise UnsupportedError(
        "Value.ReplaceType: attaching a type to a value so a later Value."
        "Type call could read it back requires a value wrapper this "
        "evaluator's flat data model does not have - the same limitation "
        "Value.ReplaceMetadata's own comment documents above for metadata"
    )


def _value_version_identity(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.VersionIdentity", args, 1)
    # "the version identity of the value, or null if it doesn't have a
    # version." No data source or connector in this evaluator models
    # versioning at all (checked: neither _sources.py nor _connectors.py
    # mentions it), so no value produced here ever has one - null is the
    # complete, provable answer, not a stand-in for an unimplemented case.
    return None


def _value_versions(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Value.Versions", args, 1)
    # Same fact as Value.VersionIdentity above: nothing here has versions,
    # so the navigation table of "available versions" is always empty.
    return []


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
    "Value.As": _value_as,
    "Value.NullableEquals": _value_nullable_equals,
    "Value.Add": _value_add,
    "Value.Subtract": _value_subtract,
    "Value.Multiply": _value_multiply,
    "Value.Divide": _value_divide,
    "Value.Metadata": _value_metadata,
    "Value.RemoveMetadata": _value_remove_metadata,
    "Value.ReplaceMetadata": _value_replace_metadata,
    "Value.Optimize": _value_optimize,
    "Value.NativeQuery": _value_native_query,
    "Type.Is": _type_is,
    "Type.IsNullable": _type_is_nullable,
    "Type.NonNullable": _type_non_nullable,
    "Type.ForRecord": _type_for_record,
    "Type.RecordFields": _type_record_fields,
    "Type.ClosedRecord": _type_closed_record,
    "Type.OpenRecord": _type_open_record,
    "Type.IsOpenRecord": _type_is_open_record,
    "Type.ForFunction": _type_for_function,
    "Type.FunctionParameters": _type_function_parameters,
    "Type.FunctionRequiredParameters": _type_function_required_parameters,
    "Type.FunctionReturn": _type_function_return,
    "Type.AddTableKey": _type_add_table_key,
    "Type.TableKeys": _type_table_keys,
    "Type.ReplaceTableKeys": _type_replace_table_keys,
    "Type.TablePartitionKey": _type_table_partition_key,
    "Type.ReplaceTablePartitionKey": _type_replace_table_partition_key,
    "Type.TableColumn": _type_table_column,
    "Type.TableRow": _type_table_row,
    "Type.TableSchema": _type_table_schema,
    "Type.Facets": _type_facets,
    "Type.ReplaceFacets": _type_replace_facets,
    "Type.Union": _type_union,
    "Type.ListItem": _type_list_item,
    "Value.Alternates": _value_alternates,
    "Value.Expression": _value_expression,
    "Value.ReplaceType": _value_replace_type,
    "Value.VersionIdentity": _value_version_identity,
    "Value.Versions": _value_versions,
}
