"""``Number.*``, ``Logical.*``, and ``Json.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    _arity,
    _parse_numeric_literal,
    _require_int,
    _require_number,
    _require_str,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _number_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.From", args, 1)
    value = args[0]
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return _parse_numeric_literal(value.strip())
        except ValueError as error:
            raise EvalError(f"Number.From: not a number: {value!r}") from error
    raise EvalError(f"Number.From: unsupported value type: {_type_name(value)}")


def _number_round(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Round", args, 1, 2)
    value = _require_number(args[0])
    digits = _require_int(args[1]) if len(args) == 2 else 0
    return round(value, digits)


def _number_abs(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Abs", args, 1)
    return abs(_require_number(args[0]))


def _json_document(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Json.Document", args, 1)
    text = _require_str(args[0])
    try:
        return _json.loads(text)
    except _json.JSONDecodeError as error:
        raise EvalError(f"Json.Document: invalid JSON: {error}") from error


def _logical_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Logical.From", args, 1)
    value = args[0]
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
        raise EvalError(f"Logical.From: not a logical value: {value!r}")
    raise EvalError(f"Logical.From: unsupported value type: {_type_name(value)}")


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
BUILTINS: dict[str, Any] = {
    "Number.From": _number_from,
    "Number.Round": _number_round,
    "Number.Abs": _number_abs,
    "Json.Document": _json_document,
    "Logical.From": _logical_from,
}
