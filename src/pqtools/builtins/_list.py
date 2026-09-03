"""``List.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.

``List.Transform``/``List.Select`` call back into M lambdas via
``ctx.invoke`` rather than a module-level ``_invoke`` import - see the
``_Ctx.invoke`` docstring in ``evaluate.py`` for why.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
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


def _consume_budget(ctx: _Ctx, count: int) -> None:
    """Charge `count` steps against ctx.budget before an operation whose
    cost scales with a caller-supplied count (List.Numbers/List.Repeat), so
    a huge count fails fast with EvalError instead of allocating an
    unbounded list. See PRD-0.5.0-builtins.md correctness rule 6.
    """
    for _ in range(count):
        ctx.budget.tick()


def _list_combine(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Combine", args, 1)
    result: list[Any] = []
    for sublist in _require_list(args[0]):
        result.extend(_require_list(sublist))
    return result


def _list_remove_nulls(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.RemoveNulls", args, 1)
    return [item for item in _require_list(args[0]) if item is not None]


def _list_remove_items(args: list[Any], ctx: _Ctx) -> Any:
    # List.RemoveItems(list1, list2) - removes EVERY occurrence of every
    # value found in list2, not a multiset (one-per-match) removal.
    # Verified against the MS docs worked example: removing {2, 4, 6} from
    # {1, 2, 3, 4, 2, 5, 5} drops BOTH 2's, giving {1, 3, 5, 5}.
    _arity("List.RemoveItems", args, 2)
    items1 = _require_list(args[0])
    items2 = _require_list(args[1])
    return [x for x in items1 if not any(_m_equal(x, y) for y in items2)]


def _list_zip(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Zip", args, 1)
    lists = [_require_list(lst) for lst in _require_list(args[0])]
    if not lists:
        return []
    longest = max(len(lst) for lst in lists)
    result = []
    for i in range(longest):
        result.append([lst[i] if i < len(lst) else None for lst in lists])
    return result


def _list_positions(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Positions", args, 1)
    return list(range(len(_require_list(args[0]))))


def _list_accumulate(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Accumulate", args, 3)
    items = _require_list(args[0])
    state = args[1]
    accumulator = args[2]
    for item in items:
        state = ctx.invoke(accumulator, [state, item], ctx)
    return state


def _list_numbers(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Numbers", args, 2, 3)
    start = _require_number(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("List.Numbers: count must not be negative")
    increment = (
        _require_number(args[2]) if len(args) == 3 and args[2] is not None else 1
    )
    _consume_budget(ctx, count)
    return [start + increment * i for i in range(count)]


def _is_count(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _list_skip(args: list[Any], ctx: _Ctx) -> Any:
    # List.Skip(list, optional countOrCondition) - trap: the default (arg
    # omitted or null) is NOT "skip 0", it is "skip the first ELEMENT"
    # (count = 1), per the real docs ("Returns a list that skips the first
    # element of list list").
    _arity("List.Skip", args, 1, 2)
    items = _require_list(args[0])
    count_or_condition = args[1] if len(args) == 2 else None
    if count_or_condition is None:
        return items[1:]
    if _is_count(count_or_condition):
        count = _require_int(count_or_condition)
        if count < 0:
            raise EvalError("List.Skip: count must not be negative")
        return items[count:]
    idx = 0
    for item in items:
        keep = ctx.invoke(count_or_condition, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.Skip: condition must return a logical value")
        if not keep:
            break
        idx += 1
    return items[idx:]


def _list_first_n(args: list[Any], ctx: _Ctx) -> Any:
    # List.FirstN(list, countOrCondition) - countOrCondition is REQUIRED in
    # the real signature (unlike List.Skip/List.LastN's optional form).
    _arity("List.FirstN", args, 2)
    items = _require_list(args[0])
    count_or_condition = args[1]
    if _is_count(count_or_condition):
        count = _require_int(count_or_condition)
        if count < 0:
            raise EvalError("List.FirstN: count must not be negative")
        return items[:count]
    result = []
    for item in items:
        keep = ctx.invoke(count_or_condition, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.FirstN: condition must return a logical value")
        if not keep:
            break
        result.append(item)
    return result


def _list_last_n(args: list[Any], ctx: _Ctx) -> Any:
    # List.LastN(list, optional countOrCondition) - trap: although the
    # parameter is documented as optional, real PQ errors if it is omitted
    # or null (verified against the docs' own caveat).
    _arity("List.LastN", args, 1, 2)
    items = _require_list(args[0])
    count_or_condition = args[1] if len(args) == 2 else None
    if count_or_condition is None:
        raise EvalError("List.LastN: countOrCondition is required")
    if _is_count(count_or_condition):
        count = _require_int(count_or_condition)
        if count < 0:
            raise EvalError("List.LastN: count must not be negative")
        return items[len(items) - count :]
    result = []
    for item in reversed(items):
        keep = ctx.invoke(count_or_condition, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.LastN: condition must return a logical value")
        if not keep:
            break
        result.append(item)
    result.reverse()
    return result


def _list_contains_any(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.ContainsAny", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("List.ContainsAny: equationCriteria argument")
    items = _require_list(args[0])
    values = _require_list(args[1])
    return any(any(_m_equal(item, value) for item in items) for value in values)


def _list_contains_all(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.ContainsAll", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("List.ContainsAll: equationCriteria argument")
    items = _require_list(args[0])
    values = _require_list(args[1])
    return all(any(_m_equal(item, value) for item in items) for value in values)


def _list_difference(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Difference", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("List.Difference: equationCriteria argument")
    items1 = _require_list(args[0])
    items2 = _require_list(args[1])
    return [x for x in items1 if not any(_m_equal(x, y) for y in items2)]


def _list_intersect(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Intersect", args, 1, 2)
    if len(args) == 2 and args[1] is not None:
        raise UnsupportedError("List.Intersect: equationCriteria argument")
    sublists = [_require_list(lst) for lst in _require_list(args[0])]
    if not sublists:
        return []
    result: list[Any] = []
    for item in sublists[0]:
        if any(_m_equal(item, seen) for seen in result):
            continue
        if all(any(_m_equal(item, x) for x in lst) for lst in sublists[1:]):
            result.append(item)
    return result


def _list_union(args: list[Any], ctx: _Ctx) -> Any:
    # List.Union dedupes across all input lists (verified: List.Union({{1,
    # 1, 2}, {2, 3}}) = {1, 2, 3}), unlike List.Combine which keeps dupes.
    _arity("List.Union", args, 1, 2)
    if len(args) == 2 and args[1] is not None:
        raise UnsupportedError("List.Union: equationCriteria argument")
    result: list[Any] = []
    for sublist in _require_list(args[0]):
        for item in _require_list(sublist):
            if not any(_m_equal(item, seen) for seen in result):
                result.append(item)
    return result


def _list_repeat(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Repeat", args, 2)
    items = _require_list(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("List.Repeat: count must not be negative")
    _consume_budget(ctx, count * len(items))
    return items * count


def _list_split(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Split", args, 2)
    items = _require_list(args[0])
    page_size = _require_int(args[1])
    if page_size <= 0:
        raise EvalError("List.Split: pageSize must be positive")
    return [items[i : i + page_size] for i in range(0, len(items), page_size)]


def _list_median(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Median", args, 1)
    items = _require_list(args[0])
    if not items:
        return None
    numbers = sorted(_require_number(x) for x in items)
    n = len(numbers)
    mid = n // 2
    if n % 2:
        return numbers[mid]
    return (numbers[mid - 1] + numbers[mid]) / 2


def _list_mode(args: list[Any], ctx: _Ctx) -> Any:
    # List.Mode: on a frequency tie, the LAST tied value wins (verified
    # against the MS docs example: {"A",1,2,3,3,4,5,5} -> 5, not 3 - both
    # appear twice, 5's occurrences finish later in the list).
    _arity("List.Mode", args, 1, 2)
    if len(args) == 2 and args[1] is not None:
        raise UnsupportedError("List.Mode: equationCriteria argument")
    items = _require_list(args[0])
    if not items:
        raise EvalError("List.Mode: list must not be empty")
    groups: list[list[Any]] = []  # [value, count] pairs, first-occurrence order
    for item in items:
        for group in groups:
            if _m_equal(group[0], item):
                group[1] += 1
                break
        else:
            groups.append([item, 1])
    best_value: Any = None
    best_count = -1
    for value, count in groups:
        if count >= best_count:
            best_count = count
            best_value = value
    return best_value


def _list_standard_deviation(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.StandardDeviation", args, 1)
    items = _require_list(args[0])
    if not items:
        raise EvalError("List.StandardDeviation: list must not be empty")
    numbers = [_require_number(x) for x in items]
    n = len(numbers)
    if n < 2:
        raise EvalError("List.StandardDeviation: at least two values are required")
    mean = sum(numbers) / n
    variance = sum((x - mean) ** 2 for x in numbers) / (n - 1)
    return math.sqrt(variance)


def _percentile_excel_inc(sorted_numbers: list[float], p: float) -> float:
    n = len(sorted_numbers)
    if n == 0:
        raise EvalError("List.Percentile: list must not be empty")
    if n == 1:
        return sorted_numbers[0]
    rank = p * (n - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_numbers[int(rank)]
    fraction = rank - lower
    return sorted_numbers[lower] + fraction * (
        sorted_numbers[upper] - sorted_numbers[lower]
    )


def _list_percentile(args: list[Any], ctx: _Ctx) -> Any:
    # Default interpolation is PercentileMode.ExcelInc (verified against
    # the docs' own worked example). PercentileMode overrides are not
    # implemented - a non-null options record raises UnsupportedError.
    _arity("List.Percentile", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("List.Percentile: options argument (PercentileMode)")
    numbers = sorted(_require_number(x) for x in _require_list(args[0]))
    percentiles = args[1]
    if isinstance(percentiles, list):
        return [_percentile_excel_inc(numbers, _require_number(p)) for p in percentiles]
    return _percentile_excel_inc(numbers, _require_number(percentiles))


def _list_all_true(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.AllTrue", args, 1)
    items = _require_list(args[0])
    for item in items:
        if not isinstance(item, bool):
            raise EvalError("List.AllTrue: list must contain only logical values")
    return all(items)


def _list_any_true(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.AnyTrue", args, 1)
    items = _require_list(args[0])
    for item in items:
        if not isinstance(item, bool):
            raise EvalError("List.AnyTrue: list must contain only logical values")
    return any(items)


def _list_is_empty(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.IsEmpty", args, 1)
    return len(_require_list(args[0])) == 0


def _list_non_null_count(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.NonNullCount", args, 1)
    return sum(1 for item in _require_list(args[0]) if item is not None)


def _list_buffer(args: list[Any], ctx: _Ctx) -> Any:
    # List.Buffer forces (materializes) a list to freeze it against
    # upstream changes/re-evaluation. pqtools has no lazy list values, so
    # this is a pure identity - see PRD-0.5.0-builtins.md P1.
    _arity("List.Buffer", args, 1)
    return _require_list(args[0])


def _list_generate(args: list[Any], ctx: _Ctx) -> Any:
    # List.Generate(initial, condition, next, optional selector). A
    # non-terminating condition (e.g. `each true`) must not hang.
    #
    # When condition/next are M lambdas (the normal case - `each`/`(x) =>`
    # always parse to one), every ctx.invoke call already evaluates real
    # AST nodes and _eval() ticks ctx.budget on each one (evaluate.py), so
    # the loop is bounded by max_steps on its own. But condition/next can
    # also be a *builtin* function value passed by bare identifier (e.g.
    # `Logical.From`) - ctx.invoke on a raw Python callable does NOT touch
    # ctx.budget at all, so that path alone could hang forever. The
    # explicit ctx.budget.tick() below closes that gap unconditionally, so
    # this loop is bounded regardless of what kind of function value it
    # was handed. Pinned by
    # test_list_generate_nonterminating_condition_hits_step_budget below.
    _arity("List.Generate", args, 3, 4)
    initial, condition, next_fn = args[0], args[1], args[2]
    selector = args[3] if len(args) == 4 else None
    result: list[Any] = []
    current = ctx.invoke(initial, [], ctx)
    while True:
        ctx.budget.tick()
        keep = ctx.invoke(condition, [current], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.Generate: condition must return a logical value")
        if not keep:
            break
        result.append(
            ctx.invoke(selector, [current], ctx) if selector is not None else current
        )
        current = ctx.invoke(next_fn, [current], ctx)
    return result


def _list_position_of(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.PositionOf", args, 2, 4)
    items = _require_list(args[0])
    value = args[1]
    occurrence = 0
    if len(args) >= 3 and args[2] is not None:
        occurrence = _require_int(args[2])
        if occurrence not in (0, 1, 2):
            raise UnsupportedError(
                "List.PositionOf: occurrence must be Occurrence.First (0), "
                "Occurrence.Last (1), or Occurrence.All (2)"
            )
    if len(args) == 4 and args[3] is not None:
        raise UnsupportedError("List.PositionOf: equationCriteria argument")
    positions = [i for i, item in enumerate(items) if _m_equal(item, value)]
    if occurrence == 2:
        return positions
    if not positions:
        return -1
    return positions[0] if occurrence == 0 else positions[-1]


def _list_insert_range(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.InsertRange", args, 3)
    items = _require_list(args[0])
    index = _require_int(args[1])
    if index < 0 or index > len(items):
        raise EvalError("List.InsertRange: index out of range")
    values = _require_list(args[2])
    return items[:index] + values + items[index:]


def _list_replace_value(args: list[Any], ctx: _Ctx) -> Any:
    # List.ReplaceValue(list, oldValue, newValue, replacer) - replacer is
    # called as replacer(item, oldValue, newValue) per item, matching the
    # real Replacer.ReplaceValue/Replacer.ReplaceText call shape.
    _arity("List.ReplaceValue", args, 4)
    items = _require_list(args[0])
    old_value, new_value, replacer = args[1], args[2], args[3]
    return [ctx.invoke(replacer, [item, old_value, new_value], ctx) for item in items]


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
BUILTINS: dict[str, Any] = {
    "List.Count": _list_count,
    "List.Sum": _list_sum,
    "List.Max": _list_max,
    "List.Min": _list_min,
    "List.Average": _list_average,
    "List.Transform": _list_transform,
    "List.Select": _list_select,
    "List.First": _list_first,
    "List.Last": _list_last,
    "List.Reverse": _list_reverse,
    "List.Sort": _list_sort,
    "List.Contains": _list_contains,
    "List.Distinct": _list_distinct,
    "List.Range": _list_range,
    "List.Combine": _list_combine,
    "List.RemoveNulls": _list_remove_nulls,
    "List.RemoveItems": _list_remove_items,
    "List.Zip": _list_zip,
    "List.Positions": _list_positions,
    "List.Accumulate": _list_accumulate,
    "List.Numbers": _list_numbers,
    "List.Skip": _list_skip,
    "List.FirstN": _list_first_n,
    "List.LastN": _list_last_n,
    "List.ContainsAny": _list_contains_any,
    "List.ContainsAll": _list_contains_all,
    "List.Difference": _list_difference,
    "List.Intersect": _list_intersect,
    "List.Union": _list_union,
    "List.Repeat": _list_repeat,
    "List.Split": _list_split,
    "List.Median": _list_median,
    "List.Mode": _list_mode,
    "List.StandardDeviation": _list_standard_deviation,
    "List.Percentile": _list_percentile,
    "List.AllTrue": _list_all_true,
    "List.AnyTrue": _list_any_true,
    "List.IsEmpty": _list_is_empty,
    "List.NonNullCount": _list_non_null_count,
    "List.Buffer": _list_buffer,
    "List.Generate": _list_generate,
    "List.PositionOf": _list_position_of,
    "List.InsertRange": _list_insert_range,
    "List.ReplaceValue": _list_replace_value,
}
