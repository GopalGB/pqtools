"""``List.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.

``List.Transform``/``List.Select`` call back into M lambdas via
``ctx.invoke`` rather than a module-level ``_invoke`` import - see the
``_Ctx.invoke`` docstring in ``evaluate.py`` for why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    _arity,
    _m_equal,
    _require_int,
    _require_list,
    _require_number,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _list_count(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Count", args, 1)
    return len(_require_list(args[0]))


def _list_sum(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Sum", args, 1)
    total: int | float = 0
    for item in _require_list(args[0]):
        total = total + _require_number(item)
    return total


def _list_max(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Max", args, 1, 2)
    items = _require_list(args[0])
    if not items:
        return args[1] if len(args) == 2 else None
    try:
        return max(items)
    except TypeError as error:
        raise EvalError("List.Max: values are not comparable") from error


def _list_min(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Min", args, 1, 2)
    items = _require_list(args[0])
    if not items:
        return args[1] if len(args) == 2 else None
    try:
        return min(items)
    except TypeError as error:
        raise EvalError("List.Min: values are not comparable") from error


def _list_average(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Average", args, 1)
    items = _require_list(args[0])
    if not items:
        return None
    numbers = [_require_number(item) for item in items]
    return sum(numbers) / len(numbers)


def _list_transform(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Transform", args, 2)
    transform = args[1]
    return [ctx.invoke(transform, [item], ctx) for item in _require_list(args[0])]


def _list_select(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Select", args, 2)
    predicate = args[1]
    result = []
    for item in _require_list(args[0]):
        keep = ctx.invoke(predicate, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.Select: predicate must return a logical value")
        if keep:
            result.append(item)
    return result


def _list_first(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.First", args, 1, 2)
    items = _require_list(args[0])
    if items:
        return items[0]
    return args[1] if len(args) == 2 else None


def _list_last(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Last", args, 1, 2)
    items = _require_list(args[0])
    if items:
        return items[-1]
    return args[1] if len(args) == 2 else None


def _list_reverse(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Reverse", args, 1)
    return list(reversed(_require_list(args[0])))


def _list_sort(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Sort", args, 1)
    try:
        return sorted(_require_list(args[0]))
    except TypeError as error:
        raise EvalError("List.Sort: values are not comparable") from error


def _list_contains(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Contains", args, 2)
    return any(_m_equal(item, args[1]) for item in _require_list(args[0]))


def _list_distinct(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Distinct", args, 1)
    result: list[Any] = []
    for item in _require_list(args[0]):
        if not any(_m_equal(item, seen) for seen in result):
            result.append(item)
    return result


def _list_range(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Range", args, 2, 3)
    items = _require_list(args[0])
    offset = _require_int(args[1])
    if offset < 0:
        raise EvalError("List.Range: offset must not be negative")
    if len(args) == 3:
        count = _require_int(args[2])
        if count < 0:
            raise EvalError("List.Range: count must not be negative")
        return items[offset : offset + count]
    return items[offset:]
