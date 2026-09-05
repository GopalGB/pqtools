"""``List.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.

``List.Transform``/``List.Select`` call back into M lambdas via
``ctx.invoke`` rather than a module-level ``_invoke`` import - see the
``_Ctx.invoke`` docstring in ``evaluate.py`` for why.
"""

from __future__ import annotations

import datetime
import decimal
import functools
import math
import random as _random
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _field_name_list,
    _m_equal,
    _require_int,
    _require_list,
    _require_number,
    _require_record,
    _require_str,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _list_count(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Count", args, 1)
    return len(_require_list(args[0]))


def _list_sum(args: list[Any], ctx: _Ctx) -> Any:
    # "Returns the sum of the NON-NULL values in the list. Returns null if
    # there are no non-null values" - verbatim from the reference, and both
    # halves were wrong here: any null raised, and an empty list returned 0,
    # a number where Power Query gives null. List.Product - the same family,
    # written later - already had both. Two members of one family written in
    # two batches diverge unless the family is checked as a family.
    _arity("List.Sum", args, 1, 2)
    numbers = [
        _require_number(item) for item in _require_list(args[0]) if item is not None
    ]
    precision = _precision_argument("List.Sum", args, 1)
    if not numbers:
        return None
    if precision == _PRECISION_DECIMAL:
        total = sum(
            (decimal.Decimal(str(number)) for number in numbers),
            decimal.Decimal(0),
        )
        if all(isinstance(number, int) for number in numbers):
            return int(total)
        return float(total)
    result: int | float = 0
    for number in numbers:
        result = result + number
    return result


def _list_extreme(name: str, args: list[Any], ctx: _Ctx, *, largest: bool) -> Any:
    """List.Max / List.Min - one body, because they share one signature.

        List.Max(list, optional default, optional comparisonCriteria,
                 optional includeNulls)

    Only the first two arguments were accepted, so the reference's own
    List.Max Example 4 - which keys German date text through
    ``Date.FromText(_, [Culture = "de-DE"])`` and returns the ORIGINAL text -
    could not run at all.
    """
    _arity(name, args, 1, 4)
    items = _require_list(args[0])
    default = args[1] if len(args) >= 2 else None

    # "includeNulls: Indicates whether null values in the list should be
    # included in determining the maximum item. The default value is true."
    # Nulls therefore participate by default, and M orders null below every
    # other value (the same rule _type.py's Value.Compare already encodes),
    # so List.Min({1, null, 3}) is null while List.Max is 3. No Microsoft
    # example pins that; it is the direct consequence of the documented
    # default plus the documented ordering, and test_list_criteria.py says so.
    include_nulls = True
    if len(args) == 4 and args[3] is not None:
        if not isinstance(args[3], bool):
            raise EvalError(f"{name}: includeNulls must be a logical value")
        include_nulls = args[3]
    if not include_nulls:
        items = [item for item in items if item is not None]
    if not items:
        return default

    key, _ = _comparison_criteria_key(
        args[2] if len(args) >= 3 else None, ctx, name, allow_order=False
    )
    pick = max if largest else min
    try:
        return pick(items, key=_null_lowest(key))
    except TypeError as error:
        raise EvalError(f"{name}: values are not comparable") from error


def _list_max(args: list[Any], ctx: _Ctx) -> Any:
    return _list_extreme("List.Max", args, ctx, largest=True)


def _list_min(args: list[Any], ctx: _Ctx) -> Any:
    return _list_extreme("List.Min", args, ctx, largest=False)


# "Only works with number, date, time, datetime, datetimezone and duration
# values" - List.Average's own About text, and "the result is given in the
# same datatype as the values in the list". The number-only version could not
# run the page's own Example 2, which averages three dates and gets a date.
_AVERAGE_DOMAIN = (
    "List.Average only works with number, date, time, datetime, "
    "datetimezone and duration values"
)


def _list_average(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Average", args, 1, 2)
    items = _require_list(args[0])
    precision = _precision_argument("List.Average", args, 1)
    if not items:
        return None

    if all(_is_plain_number(item) for item in items):
        numbers = [_require_number(item) for item in items]
        if precision == _PRECISION_DECIMAL:
            total = sum(
                (decimal.Decimal(str(number)) for number in numbers),
                decimal.Decimal(0),
            )
            return float(total / len(numbers))
        return sum(numbers) / len(numbers)

    kinds = {_average_kind(item) for item in items}
    if None in kinds or len(kinds) != 1:
        raise EvalError(f"{_AVERAGE_DOMAIN} (and all of one type)")
    kind = kinds.pop()

    if kind == "duration":
        mean = sum(item.total_seconds() for item in items) / len(items)
        return datetime.timedelta(seconds=mean)
    if kind == "time":
        mean = sum(_time_seconds(item) for item in items) / len(items)
        return (datetime.datetime.min + datetime.timedelta(seconds=mean)).time()
    if kind == "date":
        # date is the one coarse-grained case: its unit is a whole day, so a
        # mean that lands mid-day has no date to be "the same datatype" as.
        # Microsoft documents neither a rounding rule nor a datetime result
        # for it, so the ambiguous case is refused rather than guessed. The
        # page's own example averages to an exact day and runs.
        total = sum(item.toordinal() for item in items)
        if total % len(items):
            raise UnsupportedError(
                "List.Average of dates whose mean falls between two days: the "
                "reference says the result keeps the input datatype but does "
                "not define how a fractional day is rounded"
            )
        return datetime.date.fromordinal(total // len(items))

    # datetime and datetimezone: microsecond resolution, so the mean is
    # exact. A datetimezone list must share one offset - averaging values
    # from two different offsets has no documented answer.
    offsets = {item.utcoffset() for item in items}
    if len(offsets) != 1:
        raise UnsupportedError(
            "List.Average of datetimezone values with different UTC offsets "
            "is not defined by the reference"
        )
    anchor = items[0]
    mean = sum((item - anchor).total_seconds() for item in items) / len(items)
    return anchor + datetime.timedelta(seconds=mean)


def _is_plain_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _average_kind(value: Any) -> str | None:
    """Which of List.Average's documented temporal families a value is in."""
    if isinstance(value, datetime.timedelta):
        return "duration"
    if isinstance(value, datetime.time):
        return "time"
    if isinstance(value, datetime.datetime):
        # datetime before date: datetime subclasses date in Python.
        return "datetimezone" if value.tzinfo is not None else "datetime"
    if isinstance(value, datetime.date):
        return "date"
    return None


def _time_seconds(value: datetime.time) -> float:
    return (
        value.hour * 3600
        + value.minute * 60
        + value.second
        + value.microsecond / 1_000_000
    )


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
    # comparisonCriteria was absent, so `List.Sort({2, 3, 1},
    # Order.Descending)` - the page's own Example 2 - did not run.
    _arity("List.Sort", args, 1, 2)
    key, reverse = _comparison_criteria_key(
        args[1] if len(args) == 2 else None, ctx, "List.Sort"
    )
    try:
        return sorted(_require_list(args[0]), key=_null_lowest(key), reverse=reverse)
    except TypeError as error:
        raise EvalError("List.Sort: values are not comparable") from error


def _list_contains(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Contains", args, 2, 3)
    items = _require_list(args[0])
    if len(args) == 3 and args[2] is not None:
        # Example 3 passes Comparer.OrdinalIgnoreCase, the same shape
        # List.ContainsAny/List.ContainsAll already accept here.
        equal = _equation_criteria_predicate(
            args[2], ctx, "List.Contains", allow_custom_comparer=True
        )
        return any(equal(item, args[1]) for item in items)
    return any(_m_equal(item, args[1]) for item in items)


def _list_distinct(args: list[Any], ctx: _Ctx) -> Any:
    # equationCriteria was missing, so three of the page's four examples -
    # a key selector, a bare comparer, and the {selector, comparer} pair -
    # all failed on arity.
    _arity("List.Distinct", args, 1, 2)
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 2 and args[1] is not None:
        equal = _equation_criteria_predicate(
            args[1], ctx, "List.Distinct", allow_custom_comparer=True
        )
    result: list[Any] = []
    for item in _require_list(args[0]):
        if not any(equal(item, seen) for seen in result):
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


# --------------------------------------------------------------------------
# equationCriteria - the "how is equality determined" argument shared by
# List.ContainsAny/ContainsAll/Difference/Intersect/Union/Mode/PositionOf/
# PositionOfAny. Verified against the Microsoft Learn "Equation criteria"
# reference (the "Parameter values" section of
# https://learn.microsoft.com/en-us/powerquery-m/list-functions):
#
#   equationCriteria is one of
#     1. a key-selector function of one argument,
#     2. a comparer function of two arguments, or
#     3. a two-item list {keySelector, comparer}.
#
#   "In most list functions, the comparer function ... must be one of the
#   built-in comparer functions. In those list functions, using a custom
#   comparer results in an error. However, [List.Contains, List.ContainsAll,
#   List.ContainsAny, List.PositionOf and List.PositionOfAny] allow you to
#   use a custom comparer."
#
# so every call site below passes `allow_custom_comparer` accordingly.
# --------------------------------------------------------------------------


def _equation_arity(value: Any) -> int | None:
    """Best-effort M-visible arity of an equationCriteria function value.

    An `each .../(params) => ...` closure is a `_Lambda` from evaluate.py -
    not importable here (the same circular-import chain `_Ctx.invoke`'s
    docstring above explains), so duck-type on its `params` slot, exactly
    as `_table_shape.py`'s `_is_invocable` already does for the same
    reason. `Comparer.Ordinal`/`Comparer.OrdinalIgnoreCase`/
    `Comparer.FromCulture` (defined in the sibling `_text.py` module, this
    project's owner of `Comparer.*`) are plain Python callables with no
    `params` slot; they self-tag `m_is_builtin_comparer = True` at
    definition so this module can recognise them without an import across
    family modules (see builtins/__init__.py's docstring on why family
    modules stay independent). Any OTHER bare function reference (some
    other builtin used without `each`) has no discoverable M arity here -
    None means "can't tell", and callers must refuse rather than guess
    which role it plays.
    """
    if hasattr(value, "params"):
        return len(value.params)
    if getattr(value, "m_is_builtin_comparer", False):
        return 2
    return None


def _is_builtin_comparer(value: Any) -> bool:
    """True only for this project's own Comparer.* functions - never for a
    user-written two-argument lambda (a `_Lambda` has a `params` slot,
    which these do not). Used to enforce the "custom comparer results in
    an error" restriction quoted above for the list functions that do not
    allow one.
    """
    return not hasattr(value, "params") and getattr(
        value, "m_is_builtin_comparer", False
    )


def _equation_match_result(raw: Any, fn_name: str) -> bool:
    """Interpret a comparer/equationCriteria function's return value as
    equal/not-equal.

    Verified against two different Microsoft Learn worked examples:
    List.Distinct passing Comparer.OrdinalIgnoreCase (returns -1/0/1, the
    Comparer.* convention - equal iff 0) and List.PositionOf's own example
    passing a bare `(x, y) => Number.Abs(x - y) <= 2` lambda that returns a
    plain logical directly. Both shapes are real, so both are honoured;
    anything else is neither.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw == 0
    raise EvalError(
        f"{fn_name}: equationCriteria function must return a logical or a "
        "number (-1, 0, or 1)"
    )


def _equation_criteria_predicate(
    criteria: Any,
    ctx: _Ctx,
    fn_name: str,
    *,
    allow_custom_comparer: bool,
) -> Callable[[Any, Any], bool]:
    """Resolve `equationCriteria` to an ``(a, b) -> bool`` equality test."""
    if isinstance(criteria, list):
        if len(criteria) != 2:
            raise UnsupportedError(
                f"{fn_name}: equationCriteria list must have exactly two "
                "items (a key selector and a comparer)"
            )
        selector, comparer = criteria
        if not allow_custom_comparer and not _is_builtin_comparer(comparer):
            raise UnsupportedError(
                f"{fn_name}: equationCriteria comparer must be "
                "Comparer.Ordinal or Comparer.OrdinalIgnoreCase here (a "
                "custom comparer is refused, matching real Power Query)"
            )

        def pair_predicate(a: Any, b: Any) -> bool:
            key_a = ctx.invoke(selector, [a], ctx)
            key_b = ctx.invoke(selector, [b], ctx)
            return _equation_match_result(
                ctx.invoke(comparer, [key_a, key_b], ctx), fn_name
            )

        return pair_predicate

    arity = _equation_arity(criteria)
    if arity == 1:

        def selector_predicate(a: Any, b: Any) -> bool:
            return _m_equal(
                ctx.invoke(criteria, [a], ctx), ctx.invoke(criteria, [b], ctx)
            )

        return selector_predicate
    if arity == 2:
        if not allow_custom_comparer and not _is_builtin_comparer(criteria):
            raise UnsupportedError(
                f"{fn_name}: equationCriteria with a custom comparer is not "
                "supported here (only Comparer.Ordinal or "
                "Comparer.OrdinalIgnoreCase, matching real Power Query)"
            )

        def comparer_predicate(a: Any, b: Any) -> bool:
            return _equation_match_result(ctx.invoke(criteria, [a, b], ctx), fn_name)

        return comparer_predicate
    raise UnsupportedError(
        f"{fn_name}: equationCriteria function of unknown arity (expected a "
        "1-argument key selector or a 2-argument comparer; wrap a bare "
        "builtin reference in `each` if it is meant as a key selector)"
    )


# --------------------------------------------------------------------------
# comparisonCriteria over list VALUES
# --------------------------------------------------------------------------
# _shared._sort_criteria parses the TABLE form, where every key is a column
# NAME. The list functions order values instead, so their criteria are
# functions and Order values. List.Sort's page enumerates exactly four shapes:
#
#     Order.Descending                 an Order enum value
#     each Text.Length(_)              a 1-argument key selector
#     {each 1 / _, Order.Descending}   a {key selector, Order} pair
#     (x, y) => Value.Compare(x, y)    a 2-argument comparer returning -1/0/1


def _order_value(value: Any) -> bool | None:
    """True for Order.Descending, False for Order.Ascending, else None.

    `bool` is a subclass of int in Python; an M logical is not an Order -
    the same guard `_shared._sort_criteria` documents for the table form.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return None if value not in (0, 1) else value == 1


def _comparison_criteria_key(
    criteria: Any,
    ctx: _Ctx,
    fn_name: str,
    *,
    allow_order: bool = True,
) -> tuple[Callable[[Any], Any], bool]:
    """Resolve comparisonCriteria to a Python ``(key, reverse)`` sort pair.

    ``allow_order`` is False for List.Max/List.Min, whose own pages define
    the parameter narrowly as "a function that's used to transform the
    values before they're compared" and show no Order form. Accepting an
    Order there would mean inventing what it does to a maximum.
    """
    if criteria is None:
        return (lambda item: item), False

    order = _order_value(criteria)
    if order is not None:
        if not allow_order:
            raise UnsupportedError(_no_order_form(fn_name))
        return (lambda item: item), order

    if isinstance(criteria, list):
        if not allow_order:
            raise UnsupportedError(_no_order_form(fn_name))
        if len(criteria) != 2:
            raise UnsupportedError(
                f"{fn_name}: comparisonCriteria list must have exactly two "
                "items (a key selector and an Order value)"
            )
        selector, direction = criteria
        descending = _order_value(direction)
        if descending is None:
            raise UnsupportedError(
                f"{fn_name}: direction must be Order.Ascending or Order.Descending"
            )
        return (lambda item: ctx.invoke(selector, [item], ctx)), descending

    arity = _equation_arity(criteria)
    if arity == 1:
        return (lambda item: ctx.invoke(criteria, [item], ctx)), False
    if arity == 2:

        def compare(left: Any, right: Any) -> int:
            return _require_int(ctx.invoke(criteria, [left, right], ctx))

        return functools.cmp_to_key(compare), False
    raise UnsupportedError(
        f"{fn_name}: comparisonCriteria function of unknown arity (expected a "
        "1-argument key selector or a 2-argument comparer returning -1, 0 or "
        "1; wrap a bare builtin reference in `each` if it is a key selector)"
    )


def _no_order_form(fn_name: str) -> str:
    return (
        f"{fn_name}: comparisonCriteria here transforms values before they are "
        "compared, and the reference defines no Order form for it - pass a key "
        "selector or a 2-argument comparer"
    )


def _null_lowest(key: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Wrap a sort key so null orders below every other value.

    That is M's own ordering - `_type.py`'s `Value.Compare` returns -1 for a
    null left operand against anything. Without it, one blank cell in a
    column turns an ordinary sort into "values are not comparable", because
    Python refuses to compare None with anything else.

    The one-element tuple for null never has its second slot read: Python
    stops at 0 < 1, so the key's own type is never compared against it.
    """

    def wrapped(item: Any) -> Any:
        value = key(item)
        return (0,) if value is None else (1, value)

    return wrapped


def _precision_argument(fn_name: str, args: list[Any], index: int) -> int | None:
    """Validate an optional `precision as nullable number` argument.

    Precision.Double = 0, Precision.Decimal = 1 - the same enum
    `_number.py`'s Number.IntegerDivide/Number.Mod expose, and the same
    validation List.Product already carried alone.
    """
    if len(args) <= index or args[index] is None:
        return None
    precision = _require_int(args[index])
    if precision not in (0, _PRECISION_DECIMAL):
        raise EvalError(
            f"{fn_name}: precision must be Precision.Double or Precision.Decimal"
        )
    return precision


def _row_equation_criteria_predicate(
    criteria: Any, ctx: _Ctx, fn_name: str
) -> Callable[[Any, Any], bool]:
    """Equation criteria in its TABLE dialect: ``(rowA, rowB) -> bool``.

    Microsoft's table-functions page defines the concept in three shapes: "a
    key selector that determines the column in the table", "a comparer
    function", or "a list of the columns in the table to apply the equality
    criteria". pqtools had implemented each half in a different function -
    Table.Distinct understood ONLY the column list, Table.RemoveMatchingRows
    understood ONLY the function forms - so each one rejected the other's
    documented examples. One resolver now, and it lives beside the list
    dialect so the two cannot quietly drift apart again.
    """
    if isinstance(criteria, str) or (
        isinstance(criteria, list)
        and criteria
        and all(isinstance(item, str) for item in criteria)
    ):
        # A bare column name is the singular of "a list of the columns";
        # Table.RemoveMatchingRows' own Example 1 passes "a", not {"a"}.
        names = _field_name_list(criteria)

        def by_columns(left: Any, right: Any) -> bool:
            return all(_m_equal(left.get(name), right.get(name)) for name in names)

        return by_columns

    if _equation_arity(criteria) == 2:
        # A comparer takes two VALUES, not two rows, so on a table it applies
        # column by column. That is not an inference: Table.RemoveMatchingRows
        # Example 2 matches the row {103, "Widget", 5} against the record
        # [OrderID = 103, Product = "widget", Quantity = 5] under
        # Comparer.OrdinalIgnoreCase, which only holds per field.
        def by_comparer(left: Any, right: Any) -> bool:
            # Compared over the fields the RIGHT operand names, not over an
            # identical key set: Table.RemoveMatchingRows matches a full row
            # against a record that may carry only the key columns, and its
            # default (no-criteria) path already works that way.
            return all(
                name in left
                and _equation_match_result(
                    ctx.invoke(criteria, [left[name], right[name]], ctx), fn_name
                )
                for name in right
            )

        return by_comparer

    return _equation_criteria_predicate(
        criteria, ctx, fn_name, allow_custom_comparer=True
    )


def _list_contains_any(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.ContainsAny", args, 2, 3)
    items = _require_list(args[0])
    values = _require_list(args[1])
    if len(args) == 3 and args[2] is not None:
        equal = _equation_criteria_predicate(
            args[2], ctx, "List.ContainsAny", allow_custom_comparer=True
        )
        return any(any(equal(item, value) for item in items) for value in values)
    return any(any(_m_equal(item, value) for item in items) for value in values)


def _list_contains_all(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.ContainsAll", args, 2, 3)
    items = _require_list(args[0])
    values = _require_list(args[1])
    if len(args) == 3 and args[2] is not None:
        equal = _equation_criteria_predicate(
            args[2], ctx, "List.ContainsAll", allow_custom_comparer=True
        )
        return all(any(equal(item, value) for item in items) for value in values)
    return all(any(_m_equal(item, value) for item in items) for value in values)


def _list_difference(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Difference", args, 2, 3)
    items1 = _require_list(args[0])
    items2 = _require_list(args[1])
    if len(args) == 3 and args[2] is not None:
        equal = _equation_criteria_predicate(
            args[2], ctx, "List.Difference", allow_custom_comparer=False
        )
        return [x for x in items1 if not any(equal(x, y) for y in items2)]
    return [x for x in items1 if not any(_m_equal(x, y) for y in items2)]


def _list_intersect(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Intersect", args, 1, 2)
    sublists = [_require_list(lst) for lst in _require_list(args[0])]
    if not sublists:
        return []
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 2 and args[1] is not None:
        equal = _equation_criteria_predicate(
            args[1], ctx, "List.Intersect", allow_custom_comparer=False
        )
    result: list[Any] = []
    for item in sublists[0]:
        if any(equal(item, seen) for seen in result):
            continue
        if all(any(equal(item, x) for x in lst) for lst in sublists[1:]):
            result.append(item)
    return result


def _list_union(args: list[Any], ctx: _Ctx) -> Any:
    # List.Union dedupes across all input lists (verified: List.Union({{1,
    # 1, 2}, {2, 3}}) = {1, 2, 3}), unlike List.Combine which keeps dupes.
    _arity("List.Union", args, 1, 2)
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 2 and args[1] is not None:
        equal = _equation_criteria_predicate(
            args[1], ctx, "List.Union", allow_custom_comparer=False
        )
    result: list[Any] = []
    for sublist in _require_list(args[0]):
        for item in _require_list(sublist):
            if not any(equal(item, seen) for seen in result):
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


def _median_midpoint(low: Any, high: Any) -> Any:
    """The average of the two middle items, or None if averaging is wrong.

    "unless the list is comprised entirely of datetimes, durations, numbers
    or times, in which case it returns the average of the two items" -
    List.Median, verbatim. DATE is pointedly absent from that list, so two
    dates take the other branch rather than averaging into a date the list
    never contained.
    """
    if isinstance(low, bool) or isinstance(high, bool):
        return None  # logical is not number in M, whatever Python thinks
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return (low + high) / 2
    if isinstance(low, datetime.timedelta) and isinstance(high, datetime.timedelta):
        return (low + high) / 2
    # datetime before date: datetime IS a date subclass in Python, and date
    # is the type the page leaves out.
    if isinstance(low, datetime.datetime) and isinstance(high, datetime.datetime):
        return low + (high - low) / 2
    if isinstance(low, datetime.time) and isinstance(high, datetime.time):
        midnight = datetime.datetime(2000, 1, 1)
        span = (datetime.datetime.combine(midnight, low) - midnight) + (
            datetime.datetime.combine(midnight, high) - midnight
        )
        return (midnight + span / 2).time()
    return None


def _list_median(args: list[Any], ctx: _Ctx) -> Any:
    # Three things the page states that this did not do. Nulls are SKIPPED
    # ("returns null if the list contains no non-null values"), the items
    # need not be numbers (the declared return type is `any`), and on an
    # even count the average is taken ONLY for datetimes, durations, numbers
    # and times - everything else takes "the smaller of the two median
    # items", which is the first of the pair in the ordering being used.
    # comparisonCriteria was missing outright.
    _arity("List.Median", args, 1, 2)
    items = [item for item in _require_list(args[0]) if item is not None]
    if not items:
        return None
    key, reverse = _comparison_criteria_key(
        args[1] if len(args) == 2 else None, ctx, "List.Median"
    )
    try:
        ordered = sorted(items, key=key, reverse=reverse)
    except TypeError as error:
        raise EvalError("List.Median: values are not comparable") from error
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    low, high = ordered[mid - 1], ordered[mid]
    midpoint = _median_midpoint(low, high)
    return low if midpoint is None else midpoint


def _list_mode(args: list[Any], ctx: _Ctx) -> Any:
    # List.Mode: on a frequency tie, the tied value whose FIRST occurrence is
    # latest wins. The MS docs example ({"A",1,2,3,3,4,5,5} -> 5, where 3 and
    # 5 both appear twice) proves a tie resolves to a later value, but not
    # which sense of "later": {5,3,3,5} gives 3 under this rule and 5 under
    # "whose last occurrence is latest", and no documented example separates
    # them. The rule below is the choice this package makes, pinned by a test
    # on that discriminating case so it cannot drift silently. Do not restate
    # it as verified - it satisfies every published example, which is a
    # weaker claim than matching the engine on every input.
    _arity("List.Mode", args, 1, 2)
    items = _require_list(args[0])
    if not items:
        raise EvalError("List.Mode: list must not be empty")
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 2 and args[1] is not None:
        equal = _equation_criteria_predicate(
            args[1], ctx, "List.Mode", allow_custom_comparer=False
        )
    groups: list[list[Any]] = []  # [value, count] pairs, first-occurrence order
    for item in items:
        for group in groups:
            if equal(group[0], item):
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


# PercentileMode.Type: ExcelInc = 1, ExcelExc = 2, SqlDisc = 3, SqlCont = 4.
# "The default behavior matches PercentileMode.ExcelInc." SqlCont is SQL
# Server's PERCENTILE_CONT, which is the same linear interpolation on rank
# p*(n-1) that Excel's PERCENTILE.INC uses, so the two share a branch.
_PERCENTILE_EXCEL_INC = 1
_PERCENTILE_EXCEL_EXC = 2
_PERCENTILE_SQL_DISC = 3
_PERCENTILE_SQL_CONT = 4


def _percentile(sorted_numbers: list[float], p: float, mode: int) -> float:
    n = len(sorted_numbers)
    if n == 0:
        raise EvalError("List.Percentile: list must not be empty")
    if not 0.0 <= p <= 1.0:
        raise EvalError(f"List.Percentile: percentile {p} must be between 0.0 and 1.0")

    if mode == _PERCENTILE_SQL_DISC:
        # PERCENTILE_DISC never interpolates: it returns an actual member of
        # the list - the first whose cumulative distribution reaches p.
        return sorted_numbers[max(math.ceil(p * n) - 1, 0)]

    if mode == _PERCENTILE_EXCEL_EXC:
        # PERCENTILE.EXC ranks over n + 1 positions and is undefined outside
        # them; Excel itself returns #NUM! there rather than clamping.
        rank = p * (n + 1) - 1
        if rank < 0 or rank > n - 1:
            raise EvalError(
                f"List.Percentile: PercentileMode.ExcelExc is undefined for "
                f"percentile {p} over {n} value(s) (it requires "
                "1/(n+1) <= p <= n/(n+1))"
            )
    else:
        rank = p * (n - 1)

    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_numbers[lower]
    fraction = rank - lower
    return sorted_numbers[lower] + fraction * (
        sorted_numbers[upper] - sorted_numbers[lower]
    )


def _list_percentile(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Percentile", args, 2, 3)
    mode = _PERCENTILE_EXCEL_INC
    if len(args) == 3 and args[2] is not None:
        options = dict(_require_record(args[2]))
        raw = options.pop("PercentileMode", None)
        if options:
            raise UnsupportedError(f"List.Percentile: option(s) {sorted(options)}")
        if raw is not None:
            mode = _require_int(raw)
            if mode not in (
                _PERCENTILE_EXCEL_INC,
                _PERCENTILE_EXCEL_EXC,
                _PERCENTILE_SQL_DISC,
                _PERCENTILE_SQL_CONT,
            ):
                raise EvalError(
                    "List.Percentile: PercentileMode must be one of "
                    "PercentileMode.ExcelInc, .ExcelExc, .SqlDisc or .SqlCont"
                )
    numbers = sorted(_require_number(x) for x in _require_list(args[0]))
    percentiles = args[1]
    if isinstance(percentiles, list):
        return [_percentile(numbers, _require_number(p), mode) for p in percentiles]
    return _percentile(numbers, _require_number(percentiles), mode)


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
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 4 and args[3] is not None:
        equal = _equation_criteria_predicate(
            args[3], ctx, "List.PositionOf", allow_custom_comparer=True
        )
    positions = [i for i, item in enumerate(items) if equal(item, value)]
    if occurrence == 2:
        return positions
    if not positions:
        return -1
    return positions[0] if occurrence == 0 else positions[-1]


def _list_position_of_any(args: list[Any], ctx: _Ctx) -> Any:
    # List.PositionOfAny(list as list, values as list, optional occurrence
    # as nullable number, optional equationCriteria as any) as any - not
    # implemented anywhere in this evaluator before now (verified: no
    # `raise UnsupportedError` site and no BUILTINS entry existed for it).
    # Signature, occurrence semantics, and equationCriteria handling
    # verified against Microsoft Learn's List.PositionOfAny page, and
    # mirror List.PositionOf above (same function, `any of values` instead
    # of a single `value`).
    _arity("List.PositionOfAny", args, 2, 4)
    items = _require_list(args[0])
    values = _require_list(args[1])
    occurrence = 0
    if len(args) >= 3 and args[2] is not None:
        occurrence = _require_int(args[2])
        if occurrence not in (0, 1, 2):
            raise UnsupportedError(
                "List.PositionOfAny: occurrence must be Occurrence.First (0), "
                "Occurrence.Last (1), or Occurrence.All (2)"
            )
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 4 and args[3] is not None:
        equal = _equation_criteria_predicate(
            args[3], ctx, "List.PositionOfAny", allow_custom_comparer=True
        )
    positions = [
        i for i, item in enumerate(items) if any(equal(item, value) for value in values)
    ]
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


# --------------------------------------------------------------------------
# 0.10.0 gap-fill - the 25 List.* names Microsoft documents that this
# package did not have. Grounded against learn.microsoft.com/en-us/
# powerquery-m/<name> (fetched fresh for this change), following each
# page's own syntax block and worked examples. Grouped in the same order
# task tracking used, not by theme.
# --------------------------------------------------------------------------


def _list_alternate(args: list[Any], ctx: _Ctx) -> Any:
    # List.Alternate(list, count, optional repeatInterval, optional offset).
    # Algorithm reverse-engineered from all 4 of the docs' own worked
    # examples (verified: every one of the 4 reproduces exactly): keep the
    # first `offset` items untouched, then repeatedly skip `count` items and
    # keep `repeatInterval` items; if `repeatInterval` is omitted, keep
    # everything after the initial skip and stop.
    _arity("List.Alternate", args, 2, 4)
    items = _require_list(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("List.Alternate: count must not be negative")
    repeat_interval: int | None = None
    if len(args) >= 3 and args[2] is not None:
        repeat_interval = _require_int(args[2])
        if repeat_interval < 0:
            raise EvalError("List.Alternate: repeatInterval must not be negative")
    offset = 0
    if len(args) == 4 and args[3] is not None:
        offset = _require_int(args[3])
        if offset < 0:
            raise EvalError("List.Alternate: offset must not be negative")
    result = list(items[:offset])
    i = offset
    n = len(items)
    # ctx.budget.tick() guards the one input the docs never address: count=0
    # AND repeatInterval=0 together never advance `i`, which would otherwise
    # hang forever (same protection List.Generate uses for the same reason).
    while i < n:
        ctx.budget.tick()
        i += count
        if repeat_interval is None:
            result.extend(items[i:])
            break
        result.extend(items[i : i + repeat_interval])
        i += repeat_interval
    return result


def _list_find_text(args: list[Any], ctx: _Ctx) -> Any:
    # List.FindText(list, text) - "the values from list which contained the
    # value text". A case-sensitive substring test (the docs' own example is
    # single-case so it can't itself prove case-sensitivity; this matches
    # Text.Contains' own default-comparer behaviour in _text.py, which IS
    # ordinal/case-sensitive unless a comparer says otherwise). Non-text
    # items cannot "contain" text, so they are excluded rather than raising
    # - a documented choice (the docs' own example never mixes types), not a
    # verified fact.
    _arity("List.FindText", args, 2)
    items = _require_list(args[0])
    text = _require_str(args[1])
    return [item for item in items if isinstance(item, str) and text in item]


def _list_is_distinct(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.IsDistinct", args, 1, 2)
    items = _require_list(args[0])
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 2 and args[1] is not None:
        equal = _equation_criteria_predicate(
            args[1], ctx, "List.IsDistinct", allow_custom_comparer=False
        )
    seen: list[Any] = []
    for item in items:
        if any(equal(item, other) for other in seen):
            return False
        seen.append(item)
    return True


def _list_matches_all(args: list[Any], ctx: _Ctx) -> Any:
    # Empty-list result is not in the docs' examples; True is the
    # conventional vacuous-truth answer (matches Python's own all([])) and
    # is pinned as a documented choice, not a verified fact.
    _arity("List.MatchesAll", args, 2)
    condition = args[1]
    for item in _require_list(args[0]):
        keep = ctx.invoke(condition, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.MatchesAll: condition must return a logical value")
        if not keep:
            return False
    return True


def _list_matches_any(args: list[Any], ctx: _Ctx) -> Any:
    # Empty-list result is not in the docs' examples; False is the
    # conventional vacuous-truth answer (matches Python's own any([])) and
    # is pinned as a documented choice, not a verified fact.
    _arity("List.MatchesAny", args, 2)
    condition = args[1]
    for item in _require_list(args[0]):
        keep = ctx.invoke(condition, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.MatchesAny: condition must return a logical value")
        if keep:
            return True
    return False


def _list_single(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Single", args, 1)
    items = _require_list(args[0])
    if not items:
        raise EvalError("List.Single: the list is empty")
    if len(items) > 1:
        raise EvalError(
            "List.Single: there were too many elements in the enumeration "
            "to complete the operation"
        )
    return items[0]


def _list_single_or_default(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.SingleOrDefault", args, 1, 2)
    items = _require_list(args[0])
    if not items:
        return args[1] if len(args) == 2 else None
    if len(items) > 1:
        raise EvalError(
            "List.SingleOrDefault: there were too many elements in the "
            "enumeration to complete the operation"
        )
    return items[0]


def _list_remove_first_n(args: list[Any], ctx: _Ctx) -> Any:
    # List.RemoveFirstN(list, optional countOrCondition) - omitted/null
    # removes exactly the first element (the docs' own base description,
    # before countOrCondition is even introduced).
    _arity("List.RemoveFirstN", args, 1, 2)
    items = _require_list(args[0])
    count_or_condition = args[1] if len(args) == 2 else None
    if count_or_condition is None:
        return items[1:]
    if _is_count(count_or_condition):
        count = _require_int(count_or_condition)
        if count < 0:
            raise EvalError("List.RemoveFirstN: count must not be negative")
        return items[count:]
    idx = 0
    for item in items:
        keep = ctx.invoke(count_or_condition, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.RemoveFirstN: condition must return a logical value")
        if not keep:
            break
        idx += 1
    return items[idx:]


def _list_remove_last_n(args: list[Any], ctx: _Ctx) -> Any:
    # List.RemoveLastN(list, optional countOrCondition) - the docs' own
    # words for the omitted/null case: "only one item is removed".
    _arity("List.RemoveLastN", args, 1, 2)
    items = _require_list(args[0])
    count_or_condition = args[1] if len(args) == 2 else None
    if count_or_condition is None:
        return items[:-1] if items else []
    if _is_count(count_or_condition):
        count = _require_int(count_or_condition)
        if count < 0:
            raise EvalError("List.RemoveLastN: count must not be negative")
        return list(items) if count == 0 else items[:-count]
    idx = len(items)
    for item in reversed(items):
        keep = ctx.invoke(count_or_condition, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.RemoveLastN: condition must return a logical value")
        if not keep:
            break
        idx -= 1
    return items[:idx]


def _list_remove_matching_items(args: list[Any], ctx: _Ctx) -> Any:
    # Same shape as the existing List.RemoveItems (removes every occurrence
    # of every value found in list2), plus the optional equationCriteria
    # List.RemoveItems does not take - kept as a separate function rather
    # than adding a parameter to List.RemoveItems, since that would change
    # an already-shipped, already-tested function's signature.
    _arity("List.RemoveMatchingItems", args, 2, 3)
    items1 = _require_list(args[0])
    items2 = _require_list(args[1])
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 3 and args[2] is not None:
        equal = _equation_criteria_predicate(
            args[2], ctx, "List.RemoveMatchingItems", allow_custom_comparer=False
        )
    return [x for x in items1 if not any(equal(x, y) for y in items2)]


def _list_remove_range(args: list[Any], ctx: _Ctx) -> Any:
    # List.RemoveRange(list, index, optional count) - the docs' only worked
    # example always passes count explicitly, so the omitted/null default is
    # NOT verified from an example. Picked: 1 (mirrors List.RemoveLastN's
    # own documented "if this parameter is null, only one item is removed"),
    # pinned as a choice by a dedicated test, not claimed as a fact.
    _arity("List.RemoveRange", args, 2, 3)
    items = _require_list(args[0])
    index = _require_int(args[1])
    if index < 0 or index > len(items):
        raise EvalError("List.RemoveRange: index out of range")
    if len(args) == 3 and args[2] is not None:
        count = _require_int(args[2])
        if count < 0:
            raise EvalError("List.RemoveRange: count must not be negative")
    else:
        count = 1
    if index + count > len(items):
        raise EvalError("List.RemoveRange: count exceeds the remaining items")
    return items[:index] + items[index + count :]


def _list_replace_matching_items(args: list[Any], ctx: _Ctx) -> Any:
    # List.ReplaceMatchingItems(list, replacements, optional equationCriteria)
    # - each replacement is a {oldValue, newValue} pair; the FIRST pair
    # whose oldValue matches an item wins (the docs' own example never has
    # two pairs match the same item, so "first wins" is a documented choice,
    # not a verified fact).
    _arity("List.ReplaceMatchingItems", args, 2, 3)
    items = _require_list(args[0])
    replacements = _require_list(args[1])
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 3 and args[2] is not None:
        equal = _equation_criteria_predicate(
            args[2], ctx, "List.ReplaceMatchingItems", allow_custom_comparer=False
        )
    pairs: list[tuple[Any, Any]] = []
    for pair in replacements:
        pair_items = _require_list(pair)
        if len(pair_items) != 2:
            raise EvalError(
                "List.ReplaceMatchingItems: each replacement must be a "
                "two-item list of {oldValue, newValue}"
            )
        pairs.append((pair_items[0], pair_items[1]))
    result = []
    for item in items:
        replaced = item
        for old, new in pairs:
            if equal(item, old):
                replaced = new
                break
        result.append(replaced)
    return result


def _list_replace_range(args: list[Any], ctx: _Ctx) -> Any:
    # List.ReplaceRange(list, index, count, replaceWith) - unlike
    # RemoveRange, `count` is REQUIRED here (not `optional` in the docs'
    # own syntax block), so there is no default to guess.
    _arity("List.ReplaceRange", args, 4)
    items = _require_list(args[0])
    index = _require_int(args[1])
    count = _require_int(args[2])
    if index < 0 or index > len(items):
        raise EvalError("List.ReplaceRange: index out of range")
    if count < 0:
        raise EvalError("List.ReplaceRange: count must not be negative")
    if index + count > len(items):
        raise EvalError("List.ReplaceRange: count exceeds the remaining items")
    replace_with = _require_list(args[3])
    return items[:index] + replace_with + items[index + count :]


def _list_transform_many(args: list[Any], ctx: _Ctx) -> Any:
    # List.TransformMany(list, collectionTransform, resultTransform) -
    # collectionTransform(x) as list projects each element into an
    # intermediate list; resultTransform(x, y) builds the final item from
    # the ORIGINAL element x and each y drawn from that intermediate list.
    _arity("List.TransformMany", args, 3)
    items = _require_list(args[0])
    collection_transform = args[1]
    result_transform = args[2]
    result = []
    for item in items:
        sub_items = _require_list(ctx.invoke(collection_transform, [item], ctx))
        for sub in sub_items:
            result.append(ctx.invoke(result_transform, [item, sub], ctx))
    return result


def _list_max_min_n(name: str, args: list[Any], ctx: _Ctx, *, descending: bool) -> Any:
    # List.MaxN/List.MinN share one shape: (list, countOrCondition, optional
    # comparisonCriteria, optional includeNulls). Two things do NOT
    # generalise across the pair, verified by reading each function's OWN
    # docs page rather than assuming symmetry:
    #   - a null countOrCondition: only List.MinN's page defines it ("the
    #     single smallest value in the list is returned"); List.MaxN's page
    #     never mentions this case, so List.MaxN refuses it.
    #   - a condition function: List.MaxN's page says "the returned list
    #     includes all items that meet the condition" (a full filter over
    #     the whole sorted list); List.MinN's page says "once an item fails
    #     the condition, no further items are considered" (stop at the
    #     first miss). Both are honoured literally below.
    _arity(name, args, 2, 4)
    items = _require_list(args[0])
    count_or_condition = args[1]
    comparison_criteria = args[2] if len(args) >= 3 else None
    include_nulls = True
    if len(args) == 4 and args[3] is not None:
        if not isinstance(args[3], bool):
            raise EvalError(f"{name}: includeNulls must be a logical value")
        include_nulls = args[3]

    if comparison_criteria is not None:
        # MaxN's own worked example (a `each Date.FromText(...)` key
        # selector) is the only shape any worked example demonstrates for
        # comparisonCriteria here - a two-argument comparer or a
        # {selector, comparer} list (the shapes equationCriteria allows
        # elsewhere in this file) are a DIFFERENT, undocumented-for-this-
        # function shape, so they are refused rather than guessed.
        if _equation_arity(comparison_criteria) != 1:
            raise UnsupportedError(
                f"{name}: comparisonCriteria must be a one-argument key "
                "selector function (the only documented/worked-example "
                "shape - a two-argument comparer or a {selector, comparer} "
                "list is not supported here)"
            )

        def key(item: Any) -> Any:
            return ctx.invoke(comparison_criteria, [item], ctx)
    else:

        def key(item: Any) -> Any:
            return item

    working = items if include_nulls else [x for x in items if x is not None]
    try:
        ordered = sorted(working, key=key, reverse=descending)
    except TypeError as error:
        # Matches this file's existing List.Max/List.Sort convention: a
        # null left in the list (includeNulls defaults to True) that then
        # meets a non-null value during sorting is "not comparable", the
        # same error those functions already raise on mixed-type input -
        # not a new invented null-ordering rule.
        raise EvalError(f"{name}: values are not comparable") from error

    if count_or_condition is None:
        if name == "List.MinN":
            return ordered[:1]
        raise UnsupportedError(
            f"{name}: countOrCondition of null is undocumented for {name} "
            "(List.MinN's own page defines this case; List.MaxN's does not)"
        )

    if _is_count(count_or_condition):
        n = _require_int(count_or_condition)
        if n < 0:
            raise EvalError(f"{name}: count must not be negative")
        return ordered[:n]

    # A condition function, invoked on the ORIGINAL item: comparisonCriteria
    # only transforms values "before they're compared" (the sort step) and
    # plays no part in the filter step.
    result: list[Any] = []
    for item in ordered:
        keep = ctx.invoke(count_or_condition, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError(f"{name}: condition must return a logical value")
        if keep:
            result.append(item)
        elif name == "List.MinN":
            break
    return result


def _list_max_n(args: list[Any], ctx: _Ctx) -> Any:
    return _list_max_min_n("List.MaxN", args, ctx, descending=True)


def _list_min_n(args: list[Any], ctx: _Ctx) -> Any:
    return _list_max_min_n("List.MinN", args, ctx, descending=False)


def _list_modes(args: list[Any], ctx: _Ctx) -> Any:
    # List.Modes returns EVERY value tied for the highest frequency (unlike
    # List.Mode above, which picks exactly one and documents its own tie
    # rule in the comment at its definition). The order returned here is
    # first-occurrence order - the docs' one worked example
    # ({"A",1,2,3,3,4,5,5} -> {3,5}) is consistent with BOTH
    # first-occurrence order and value-ascending order (3 occurs before 5,
    # AND 3 < 5), so it cannot verify which rule this package should use.
    # First-occurrence order is picked because it reuses the exact grouping
    # List.Mode already builds, needing no extra invented rule; pinned by a
    # test where the two candidate orders would actually differ.
    _arity("List.Modes", args, 1, 2)
    items = _require_list(args[0])
    if not items:
        raise EvalError("List.Modes: list must not be empty")
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 2 and args[1] is not None:
        equal = _equation_criteria_predicate(
            args[1], ctx, "List.Modes", allow_custom_comparer=False
        )
    groups: list[list[Any]] = []  # [value, count] pairs, first-occurrence order
    for item in items:
        for group in groups:
            if equal(group[0], item):
                group[1] += 1
                break
        else:
            groups.append([item, 1])
    best_count = max(count for _, count in groups)
    return [value for value, count in groups if count == best_count]


def _list_covariance(args: list[Any], ctx: _Ctx) -> Any:
    # List.Covariance is documented "as nullable number" (unlike
    # List.StandardDeviation's non-nullable `as number`, which is why THAT
    # function raises instead of returning null on too few values) - null
    # is treated as the answer for the one case with no numbers to compare:
    # both lists empty. The docs' own example ({1,2,3},{1,2,3}) -> 0.6667
    # is POPULATION covariance (sum((x-mean)(y-mean))/n, n not n-1):
    # mean=2, sum((x-2)^2)=2, 2/3=0.66666... matches exactly; sample
    # covariance (n-1=2) would give 1.0, which does not match.
    _arity("List.Covariance", args, 2)
    xs = _require_list(args[0])
    ys = _require_list(args[1])
    if len(xs) != len(ys):
        raise EvalError(
            "List.Covariance: numberList1 and numberList2 must contain the "
            "same number of values"
        )
    if not xs:
        return None
    x_values = [_require_number(x) for x in xs]
    y_values = [_require_number(y) for y in ys]
    n = len(x_values)
    mean_x = sum(x_values) / n
    mean_y = sum(y_values) / n
    pairs = zip(x_values, y_values, strict=True)
    return sum((x - mean_x) * (y - mean_y) for x, y in pairs) / n


# Precision.Double=0 / Precision.Decimal=1 - the same enum _number.py's
# Number.IntegerDivide/Number.Mod already expose as BUILTINS constants.
# Duplicated here in miniature rather than imported: family builtin modules
# stay independent by design (see builtins/__init__.py's docstring and
# _equation_arity's docstring above for why), and at the M level these are
# just plain integers by the time they reach this function's args anyway.
_PRECISION_DECIMAL = 1


def _list_product(args: list[Any], ctx: _Ctx) -> Any:
    # List.Product(numbersList, optional precision) - "the product of the
    # NON-NULL numbers", "null if there are no non-null values" (both
    # verified against the docs' own wording; the docs' single example has
    # no nulls, so the skip-nulls behaviour itself is grounded in the prose,
    # not a worked example). Precision.Decimal multiplies through
    # decimal.Decimal (str-round-tripped, exactly as _number.py's own
    # _decimal_truncate_divide does) so repeated multiplication does not
    # accumulate binary-float rounding error - a real, exactly implementable
    # difference, not a guess.
    _arity("List.Product", args, 1, 2)
    items = _require_list(args[0])
    precision = None
    if len(args) == 2 and args[1] is not None:
        precision = _require_int(args[1])
        if precision not in (0, _PRECISION_DECIMAL):
            raise EvalError(
                "List.Product: precision must be Precision.Double or Precision.Decimal"
            )
    numbers = [_require_number(x) for x in items if x is not None]
    if not numbers:
        return None
    if precision == _PRECISION_DECIMAL:
        product = decimal.Decimal(1)
        for number in numbers:
            product *= decimal.Decimal(str(number))
        if all(isinstance(number, int) for number in numbers):
            return int(product)
        return float(product)
    total: int | float = 1
    for number in numbers:
        total = total * number
    return total


def _temporal_start_type_name(value: Any) -> str:
    return type(value).__name__ if value is not None else "null"


def _generate_temporal_list(
    name: str,
    args: list[Any],
    ctx: _Ctx,
    *,
    is_start: Callable[[Any], bool],
    type_label: str,
    advance: Callable[[Any, datetime.timedelta], Any],
) -> Any:
    # Shared shape behind List.Dates/List.DateTimes/List.DateTimeZones/
    # List.Durations/List.Times: (start, count, step as duration), each
    # verified against its own docs page - all 5 share the identical
    # "returns count values starting at start, incremented by step" wording
    # and worked examples.
    _arity(name, args, 3)
    start = args[0]
    if not is_start(start):
        raise EvalError(
            f"{name}: start must be a {type_label}, got "
            f"{_temporal_start_type_name(start)}"
        )
    count = _require_int(args[1])
    if count < 0:
        raise EvalError(f"{name}: count must not be negative")
    step = args[2]
    if not isinstance(step, datetime.timedelta):
        raise EvalError(f"{name}: step must be a duration")
    _consume_budget(ctx, count)
    result: list[Any] = []
    current = start
    for _ in range(count):
        result.append(current)
        current = advance(current, step)
    return result


def _advance_plain(current: Any, step: datetime.timedelta) -> Any:
    return current + step


def _advance_time_wrapping(
    current: datetime.time, step: datetime.timedelta
) -> datetime.time:
    # datetime.time cannot represent >=24h or <0h - Python refuses `time +
    # timedelta` outright, so wrapping via modulo 24h is the only value a
    # `time` result CAN hold, not a stylistic choice among alternatives
    # (List.Times' own docs example never crosses midnight, so this is
    # pinned by a dedicated wraparound test, not a worked example).
    day_micros = 24 * 60 * 60 * 1_000_000
    current_micros = (
        current.hour * 3_600_000_000
        + current.minute * 60_000_000
        + current.second * 1_000_000
        + current.microsecond
    )
    step_micros = (step.days * 86_400 + step.seconds) * 1_000_000 + step.microseconds
    total_micros = (current_micros + step_micros) % day_micros
    hour, remainder = divmod(total_micros, 3_600_000_000)
    minute, remainder = divmod(remainder, 60_000_000)
    second, micro = divmod(remainder, 1_000_000)
    return datetime.time(int(hour), int(minute), int(second), int(micro))


def _is_date_only(value: Any) -> bool:
    return isinstance(value, datetime.date) and not isinstance(value, datetime.datetime)


def _is_naive_datetime(value: Any) -> bool:
    return isinstance(value, datetime.datetime) and value.tzinfo is None


def _is_aware_datetime(value: Any) -> bool:
    return isinstance(value, datetime.datetime) and value.tzinfo is not None


def _list_dates(args: list[Any], ctx: _Ctx) -> Any:
    return _generate_temporal_list(
        "List.Dates",
        args,
        ctx,
        is_start=_is_date_only,
        type_label="date",
        advance=_advance_plain,
    )


def _list_datetimes(args: list[Any], ctx: _Ctx) -> Any:
    return _generate_temporal_list(
        "List.DateTimes",
        args,
        ctx,
        is_start=_is_naive_datetime,
        type_label="datetime",
        advance=_advance_plain,
    )


def _list_datetimezones(args: list[Any], ctx: _Ctx) -> Any:
    # `datetimezone` values already exist in this engine - the `#datetimezone`
    # literal (`_lit_datetimezone` in _datetime.py) and `DateTime.AddZone`
    # both already produce a tz-AWARE datetime.datetime with a fixed-offset
    # tzinfo, which IS what M's datetimezone type is (a fixed UTC offset,
    # never an IANA/DST-observing zone) - verified directly:
    # `evaluate("#datetimezone(2011,12,31,23,55,0,-8,0)")` returns a
    # tzinfo=timezone(timedelta(hours=-8)) datetime. Stepping an aware
    # datetime by a timedelta preserves that same fixed tzinfo exactly, so
    # this is real datetimezone arithmetic, not naive datetimes standing in
    # for it.
    return _generate_temporal_list(
        "List.DateTimeZones",
        args,
        ctx,
        is_start=_is_aware_datetime,
        type_label="datetimezone",
        advance=_advance_plain,
    )


def _list_durations(args: list[Any], ctx: _Ctx) -> Any:
    return _generate_temporal_list(
        "List.Durations",
        args,
        ctx,
        is_start=lambda value: isinstance(value, datetime.timedelta),
        type_label="duration",
        advance=_advance_plain,
    )


def _list_times(args: list[Any], ctx: _Ctx) -> Any:
    return _generate_temporal_list(
        "List.Times",
        args,
        ctx,
        is_start=lambda value: isinstance(value, datetime.time),
        type_label="time",
        advance=_advance_time_wrapping,
    )


def _list_random(args: list[Any], ctx: _Ctx) -> Any:
    # List.Random(count, optional seed) - the docs' own seeded example
    # (seed=2 -> {0.883002, 0.245344, 0.723212}) cannot be reproduced by
    # ANY third-party implementation: Microsoft has never published the
    # algorithm behind Power Query's RNG, so there is no way to be
    # bit-for-bit compatible with it (an implementation detail, not part of
    # the M language spec - unlike, say, List.Mode's tie rule, there is no
    # authoritative source to even attempt to match). What IS part of the
    # documented CONTRACT, and what this implements exactly:
    #   - no seed -> every call returns a different list (uses the shared
    #     process-level `random` module - the same instance Number.Random
    #     in _number.py already draws from).
    #   - a seed -> every call with that SAME seed returns the SAME list
    #     (a private `random.Random(seed)` instance, so seeding does not
    #     also make an unrelated Number.Random()/unseeded List.Random() call
    #     elsewhere in the same query newly reproducible).
    #   - every value in [0, 1), as documented.
    # A test pins pqtools' OWN seeded-reproducibility contract; it does not
    # and cannot assert the exact numbers from the MS docs example.
    _arity("List.Random", args, 1, 2)
    count = _require_int(args[0])
    if count < 0:
        raise EvalError("List.Random: count must not be negative")
    _consume_budget(ctx, count)
    if len(args) == 2 and args[1] is not None:
        seed = _require_number(args[1])
        rng = _random.Random(seed)
        return [rng.random() for _ in range(count)]
    return [_random.random() for _ in range(count)]


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
    "List.PositionOfAny": _list_position_of_any,
    "List.InsertRange": _list_insert_range,
    "List.ReplaceValue": _list_replace_value,
    "List.Alternate": _list_alternate,
    "List.FindText": _list_find_text,
    "List.IsDistinct": _list_is_distinct,
    "List.MatchesAll": _list_matches_all,
    "List.MatchesAny": _list_matches_any,
    "List.Single": _list_single,
    "List.SingleOrDefault": _list_single_or_default,
    "List.RemoveFirstN": _list_remove_first_n,
    "List.RemoveLastN": _list_remove_last_n,
    "List.RemoveMatchingItems": _list_remove_matching_items,
    "List.RemoveRange": _list_remove_range,
    "List.ReplaceMatchingItems": _list_replace_matching_items,
    "List.ReplaceRange": _list_replace_range,
    "List.TransformMany": _list_transform_many,
    "List.MaxN": _list_max_n,
    "List.MinN": _list_min_n,
    "List.Modes": _list_modes,
    "List.Covariance": _list_covariance,
    "List.Product": _list_product,
    "List.Dates": _list_dates,
    "List.DateTimes": _list_datetimes,
    "List.DateTimeZones": _list_datetimezones,
    "List.Durations": _list_durations,
    "List.Random": _list_random,
    "List.Times": _list_times,
}
