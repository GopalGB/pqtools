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
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "record"
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
