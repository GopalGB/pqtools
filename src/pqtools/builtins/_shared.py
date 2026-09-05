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
