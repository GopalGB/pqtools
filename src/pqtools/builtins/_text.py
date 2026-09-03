"""``Text.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    _arity,
    _format_number,
    _require_int,
    _require_list,
    _require_str,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _text_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.From", args, 1)
    value = args[0]
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return _format_number(value)
    if isinstance(value, str):
        return value
    raise EvalError(f"Text.From: unsupported value type: {_type_name(value)}")


def _text_upper(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Upper", args, 1)
    return _require_str(args[0]).upper()


def _text_lower(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Lower", args, 1)
    return _require_str(args[0]).lower()


def _text_length(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Length", args, 1)
    return len(_require_str(args[0]))


def _text_combine(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Combine", args, 1, 2)
    texts = _require_list(args[0])
    separator = _require_str(args[1]) if len(args) == 2 else ""
    return separator.join(_require_str(item) for item in texts)


def _text_contains(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Contains", args, 2)
    return _require_str(args[1]) in _require_str(args[0])


def _text_replace(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Replace", args, 3)
    return _require_str(args[0]).replace(_require_str(args[1]), _require_str(args[2]))


def _text_split(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Split", args, 2)
    return _require_str(args[0]).split(_require_str(args[1]))


def _text_start(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Start", args, 2)
    text = _require_str(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Text.Start: count must not be negative")
    return text[:count]


def _text_end(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.End", args, 2)
    text = _require_str(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Text.End: count must not be negative")
    return text[len(text) - count :] if count else ""


def _text_trim(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Trim", args, 1, 2)
    text = _require_str(args[0])
    if len(args) == 2:
        return text.strip(_require_str(args[1]))
    return text.strip()
