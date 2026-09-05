"""Error classes and value-checking helpers shared by every builtin family.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md's "Architecture change"
section.

This module must never import ``pqtools.evaluate`` (or anything under it).
``evaluate.py`` imports the ``BUILTINS`` registry from ``pqtools.builtins``,
and ``pqtools.builtins`` imports the family modules (``_table``, ``_text``,
``_list``, ``_record``, ``_number``), which import this module for
``EvalError``/``UnsupportedError`` and the ``_require_*``/``_arity``
helpers. If this module imported back into ``evaluate.py``, that chain
would be a cycle. Its only outside dependency is ``pqtools.core``, which
sits below both ``evaluate.py`` and this package.
"""

from __future__ import annotations

import datetime
import math
from typing import Any

from ..core import MQueryError


class EvalError(MQueryError):
    code = "M_EVAL_ERROR"


class UnsupportedError(EvalError):
    code = "M_EVAL_UNSUPPORTED"


def _parse_numeric_literal(token: str) -> int | float:
    text = token.strip()
    lowered = text.lower()
    if lowered == "#infinity":
        return math.inf
    if lowered == "#nan":
        return math.nan
    if lowered.startswith("0x"):
        return int(text, 16)
    if any(marker in text for marker in (".", "e", "E")):
        return float(text)
    return int(text)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "logical"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, bytes):
        return "binary"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "record"
    # The temporal family, named as M names it. These reach users inside
    # error messages, and "timedelta" is not a type Power Query has - a
    # reader who greps their query for it finds nothing.
    if isinstance(value, datetime.datetime):
        return "datetimezone" if value.tzinfo is not None else "datetime"
    if isinstance(value, datetime.date):
        return "date"
    if isinstance(value, datetime.time):
        return "time"
    if isinstance(value, datetime.timedelta):
        return "duration"
    return type(value).__name__


def _m_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    # Binary compares by content in M. Without this clause two identical
    # binaries compared FALSE - so `List.Contains(files, someBinary)` was
    # always false and `Binary.Compress` round-trip checks silently failed
    # with no error. Exactly the failure the datetime note below describes,
    # in a type nobody thought to add when that one was fixed.
    if isinstance(left, bytes) or isinstance(right, bytes):
        return isinstance(left, bytes) and isinstance(right, bytes) and left == right
    # Temporal values compare by value in M, and this function is what `=` and
    # `<>` reach. Before this clause a date only ever compared FALSE to itself,
    # so `each [d] = #date(...)` silently filtered every row away with no error -
    # the worst failure mode available. datetime is checked before date because
    # datetime.datetime is a subclass of datetime.date; comparing the two kinds
    # is a type mismatch in M, so they must not fall into one branch.
    if isinstance(left, datetime.datetime) or isinstance(right, datetime.datetime):
        return (
            isinstance(left, datetime.datetime)
            and isinstance(right, datetime.datetime)
            # An aware and a naive datetime are different M types
            # (datetimezone vs datetime) and Python refuses to compare them.
            and (left.tzinfo is None) == (right.tzinfo is None)
            and left == right
        )
    for kind in (datetime.date, datetime.time, datetime.timedelta):
        if isinstance(left, kind) or isinstance(right, kind):
            return isinstance(left, kind) and isinstance(right, kind) and left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _m_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _m_equal(left[key], right[key]) for key in left
        )
    return False


def _format_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value.is_integer():
        return str(int(value))
    return str(value)


def _numeric_quotient(left: int | float, right: int | float) -> int | float:
    """M follows IEEE 754 here, and IEEE 754 does not raise.

    The spec's own examples are `8 / 0 // #infinity` and `0 / 0 // #nan`.
    Raising instead - which this evaluator used to do - turns a query that
    runs in Power Query into one that fails here.
    """
    if right == 0:
        if left == 0 or (isinstance(left, float) and math.isnan(left)):
            return math.nan
        # Sign of the result is sign(left) * sign(right); `copysign` reads
        # the sign of a negative zero denominator, which `right < 0` cannot.
        return math.copysign(math.inf, left) * math.copysign(1.0, right)
    return left / right


def _require_number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvalError(f"expected a number, got {_type_name(value)}")
    return value


def _require_str(value: Any) -> str:
    if not isinstance(value, str):
        raise EvalError(f"expected text, got {_type_name(value)}")
    return value


def _require_int(value: Any) -> int:
    number = _require_number(value)
    if isinstance(number, float):
        if not number.is_integer():
            raise EvalError("expected a whole number")
        return int(number)
    return number


def _require_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise EvalError(f"expected a list, got {_type_name(value)}")
    return value


def _require_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvalError(f"expected a record, got {_type_name(value)}")
    return value


def _from_text_options(name: str, rest: list[Any]) -> tuple[str | None, Any]:
    """(format, culture) for the optional 2nd argument of ``X.FromText``.

    "options: An optional record ... Format ... Culture ... To support legacy
    workflows, options can also be a text value. This has the same behavior
    as if options = [Format = null, Culture = options]."
    """
    fmt: str | None = None
    culture: Any = None
    if rest and rest[0] is not None:
        first = rest[0]
        if isinstance(first, dict):
            fmt = first.get("Format")
            culture = first.get("Culture")
            unknown = set(first) - {"Format", "Culture"}
            if unknown:
                raise UnsupportedError(f"{name}: option(s) {sorted(unknown)}")
        elif isinstance(first, str):
            culture = first
        else:
            raise EvalError(
                f"{name}: options must be text or a [Format = ...] record, "
                f"got {_type_name(first)}"
            )
    return fmt, culture


def _from_text(
    name: str,
    delegate: Any,
    options: str = "record",
    parse_format: Any = None,
) -> Any:
    """Build ``X.FromText`` from the family's own ``X.From``.

    Delegating rather than re-parsing is the point: a second parser for the
    same family is a second set of edge cases, and the day they disagree the
    symptom is a value that converts one way through a column type and
    another way through an explicit call. M draws the text-only line too, so
    ``Number.FromText(1)`` is an error there as well - accepting it would let
    a column that never held text report a successful text conversion.

    ``options`` is the second parameter's shape, taken from each function's
    own Syntax block rather than assumed uniform across the family, because
    it is not uniform:

        "record"   Date/DateTime/Time - `optional options`, a
                   [Format = ..., Culture = ...] record or a legacy culture
                   text value
        "culture"  Number - `optional culture as nullable text`, no record
        "none"     Duration/Logical - `(text as nullable text)`, one
                   argument. pqtools accepted a second one, which is the
                   same defect as an invented function name: the call runs
                   here and fails in Power Query.

    ``parse_format`` is the family's ``[Format = ...]`` parser, supplied by
    the temporal module (this one cannot import it - ``_datetime`` imports
    ``_shared``). Without it a Format string is refused rather than
    silently ignored, which is the behaviour Number/Duration/Logical keep:
    none of them documents a Format option at all.

    There were two copies of this factory, one per module, differing only in
    what they imported. Table.Sort had two copies of its criteria parser and
    both carried the same bug, so this one lives in exactly one place.
    """
    if options not in ("record", "culture", "none"):
        raise ValueError(f"unknown options shape: {options}")

    def run(args: list[Any], ctx: Any) -> Any:
        _arity(name, args, 1, 1 if options == "none" else 2)
        fmt = None
        if options == "record":
            fmt, culture = _from_text_options(name, args[1:])
        elif options == "culture":
            culture = args[1] if len(args) == 2 else None
            if isinstance(culture, dict):
                raise EvalError(
                    f"{name}: the second argument is a culture text value, "
                    "not an options record"
                )
        else:
            culture = None
        # "parsing", not the module default "formatting": this direction reads
        # text, and a message about formatting sends the reader to the wrong
        # half of the round trip.
        _check_invariant_culture(name, culture, "culture-specific parsing")
        value = args[0]
        if value is None:
            return None
        if not isinstance(value, str):
            raise EvalError(f"{name}: expected text, got {_type_name(value)}")
        if fmt is not None:
            if parse_format is None:
                raise UnsupportedError(
                    f"{name}: a custom/standard Format string for parsing "
                    "(as opposed to formatting) is not implemented"
                )
            return parse_format(name, value, fmt)
        try:
            return delegate([value], ctx)
        except EvalError as error:
            # The delegate reports itself, so a failed Number.FromText would
            # otherwise say "Number.From: not a number" and send the reader
            # looking for a call that is not in their query.
            message = str(error)
            prefix = f"{name.split('Text')[0]}: "
            if message.startswith(prefix):
                message = f"{name}: {message[len(prefix) :]}"
            raise EvalError(message) from error

    run.__name__ = f"_{name.replace('.', '_').lower()}"
    return run


def _require_table(value: Any) -> list[dict[str, Any]]:
    rows = _require_list(value)
    for row in rows:
        if not isinstance(row, dict):
            raise EvalError("expected a table (a list of records)")
    return rows


def _field_name_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [_require_str(item) for item in value]
    raise EvalError("expected a field name or a list of field names")


def _arity(name: str, args: list[Any], low: int, high: int | None = None) -> None:
    ceiling = low if high is None else high
    if not (low <= len(args) <= ceiling):
        raise UnsupportedError(f"{name} with {len(args)} argument(s)")


# Cultures whose number/date formatting is identical to the invariant culture,
# which is the only one implemented. Anything else refuses BY NAME rather than
# silently formatting German data with English separators - a wrong separator
# is not a cosmetic defect, it turns 1.234 into 1234.
_INVARIANT_CULTURES = frozenset({"en-us", "en"})


def _check_invariant_culture(name: str, culture: Any, detail: str) -> None:
    """Accept null or an en-US-equivalent culture tag; refuse the rest."""
    if culture is None:
        return
    text = _require_str(culture)
    if text.strip().lower() not in _INVARIANT_CULTURES:
        raise UnsupportedError(f"{name}: {detail} for {text!r} {_CULTURE_SCOPE}")


_CULTURE_SCOPE = "(pqtools only implements invariant/en-US)"


def _null_propagates(args: list[Any]) -> bool:
    """`f(null)` is `null` wherever the page declares parameter one nullable.

    `Text.Lower(text as nullable text, optional culture as nullable text) as
    nullable text` - BOTH halves of that signature say it: null is an
    accepted input, and null is a possible output. A function that declared a
    nullable return and could never return null would be mis-declared.

    97 builtins here already behaved this way and 14 raised "expected text,
    got null" instead - the same inconsistency 14 times, invisible to every
    test because each one was individually plausible. The rule now has one
    home and `test_documented_signatures.py` asks it of every builtin.
    """
    return bool(args) and args[0] is None


def _column_selection(value: Any, what: str) -> list[str] | None:
    """`columns` as either a list of names or a table TYPE.

    "columns: (Optional) A list of the table's column names, or the table's
    type" - Table.FromRecords, verbatim. Only the names are read: which
    columns the result has, and in what order, is all this argument decides
    here. The declared field TYPES are not applied, because Power Query
    ascribes them without converting - `type table [A = number]` over text
    data does not parse the text - so honouring the names and ignoring the
    types is the faithful half, not a shortcut.
    """
    if value is None:
        return None
    # Imported inside the branch for the reason Table.AddColumn documents:
    # _type imports nothing from here, and the common path should not pay.
    from ._type import _MType

    if isinstance(value, _MType):
        if value.field_names is None:
            raise EvalError(
                f"{what}: that type value names no columns (expected a table "
                "type such as `type table [A = text, B = number]`)"
            )
        return list(value.field_names)
    return _field_name_list(value)


# MissingField.Error / Ignore / UseNull, the real M enum values (0 / 1 / 2,
# confirmed against the MissingField.Type page). This lived in _record.py
# while three Table.* functions needed it too, and a fourth
# (Table.ReorderColumns) simply refused the argument for want of it.
_MISSING_FIELD_ERROR = 0
_MISSING_FIELD_IGNORE = 1
_MISSING_FIELD_USE_NULL = 2


def _missing_field_mode(value: Any) -> int:
    """Validate an optional `missingField as nullable number` argument."""
    if value is None:
        return _MISSING_FIELD_ERROR
    mode = _require_int(value)
    if mode not in (
        _MISSING_FIELD_ERROR,
        _MISSING_FIELD_IGNORE,
        _MISSING_FIELD_USE_NULL,
    ):
        raise UnsupportedError(
            "missingField must be MissingField.Error (0), MissingField.Ignore "
            "(1), or MissingField.UseNull (2)"
        )
    return mode


def _sort_criteria(spec: Any, what: str) -> list[tuple[str, bool]]:
    """Parse Table.Sort/Max/Min ``comparisonCriteria`` into (column, descending).

    One implementation, because there were two and they had the same bug. Both
    enumerated the accepted shapes and both missed the one Microsoft's own
    Table.Sort Example 2 uses verbatim:

        Table.Sort(t, {"OrderID", Order.Descending})

    - a BARE pair, not wrapped in an outer list. pqtools rejected it with
    "entries must be a column name or {"Column", Order.Ascending}", so a query
    copied straight out of the reference did not run.

    The bare pair is unambiguous despite looking like a two-column list:
    column names are text, so a NUMBER in second position can only be an
    Order value. ``{"a", "b"}`` therefore stays two columns, and
    ``{"a", Order.Descending}`` is one column descending.

    Accepted, all of which Power Query emits:
        "Col"                                       one column, ascending
        {"A", "B"}                                  several columns, ascending
        {"Col", Order.Descending}                   bare pair (docs Example 2)
        {{"Col", Order.Descending}}                 wrapped pair
        {{"A", Order.Ascending}, "B"}               mixed (docs Example 3)
    """
    if isinstance(spec, str):
        return [(spec, False)]
    if not isinstance(spec, list):
        raise UnsupportedError(
            f"{what} with a {type(spec).__name__} comparisonCriteria"
        )

    def order_value(value: Any) -> bool | None:
        # `bool` is a subclass of int in Python; an M logical is not an Order.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return None if value not in (0, 1) else value == 1

    if len(spec) == 2 and isinstance(spec[0], str):
        descending = order_value(spec[1])
        if descending is not None:
            return [(spec[0], descending)]

    keys: list[tuple[str, bool]] = []
    for entry in spec:
        if isinstance(entry, str):
            keys.append((entry, False))
            continue
        if (
            isinstance(entry, list)
            and 1 <= len(entry) <= 2
            and isinstance(entry[0], str)
        ):
            if len(entry) == 1:
                keys.append((entry[0], False))
                continue
            descending = order_value(entry[1])
            if descending is None:
                raise UnsupportedError(
                    f"{what}: direction must be Order.Ascending or Order.Descending"
                )
            keys.append((entry[0], descending))
            continue
        raise UnsupportedError(
            f"{what}: comparisonCriteria entries must be a column name, "
            '{"Column", Order.Ascending}, or a list of those'
        )
    return keys
