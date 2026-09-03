"""``Number.*``, ``Logical.*``, and ``Json.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.
"""

from __future__ import annotations

import json as _json
import math
import random as _random
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _format_number,
    _parse_numeric_literal,
    _require_int,
    _require_number,
    _require_str,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _consume_budget(ctx: _Ctx, count: int) -> None:
    """Charge `count` steps against ctx.budget before an operation whose
    cost scales with a caller-supplied count (e.g. Number.Factorial), so a
    huge count fails fast with EvalError instead of hanging the process.
    See PRD-0.5.0-builtins.md correctness rule 6.
    """
    for _ in range(count):
        ctx.budget.tick()


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


def _number_integer_divide(args: list[Any], ctx: _Ctx) -> Any:
    # Number.IntegerDivide(number1, number2, optional precision) - integer
    # portion of number1/number2, TRUNCATED toward zero (verified: 8.3/3 =
    # 2, matching Python's math.trunc, not floor division).
    _arity("Number.IntegerDivide", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("Number.IntegerDivide: precision argument")
    number1, number2 = args[0], args[1]
    if number1 is None or number2 is None:
        return None
    number1 = _require_number(number1)
    number2 = _require_number(number2)
    if number2 == 0:
        raise EvalError("Number.IntegerDivide: division by zero")
    return math.trunc(number1 / number2)


def _number_mod(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Mod(number, divisor, optional precision) - trap (verified):
    # this is TRUNCATED (C-style) modulo, not Python's floored `%`.
    # Number.Mod(-7, 3) is -1 in real PQ, NOT the 2 that -7 % 3 gives in
    # Python. math.fmod matches PQ's truncation convention exactly.
    _arity("Number.Mod", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("Number.Mod: precision argument")
    number, divisor = args[0], args[1]
    if number is None or divisor is None:
        return None
    number = _require_number(number)
    divisor = _require_number(divisor)
    if divisor == 0:
        raise EvalError("Number.Mod: division by zero")
    result = math.fmod(number, divisor)
    if isinstance(number, int) and isinstance(divisor, int):
        return int(result)
    return result


def _number_power(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Power", args, 2)
    base, exponent = args[0], args[1]
    if base is None or exponent is None:
        return None
    return _require_number(base) ** _require_number(exponent)


def _number_sqrt(args: list[Any], ctx: _Ctx) -> Any:
    # Verified: a negative input returns Number.NaN, it does NOT raise.
    _arity("Number.Sqrt", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    if value < 0:
        return math.nan
    return math.sqrt(value)


def _number_exp(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Exp", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    try:
        return math.exp(value)
    except OverflowError:
        return math.inf


def _number_ln(args: list[Any], ctx: _Ctx) -> Any:
    # No domain-error case is documented for Number.Ln; by analogy with the
    # DOCUMENTED Number.Sqrt(negative) -> NaN behaviour (same "impossible
    # real result" family), a non-positive input returns NaN here too
    # rather than raising. Flagged as an analogy, not a confirmed doc case.
    _arity("Number.Ln", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    if value <= 0:
        return math.nan
    return math.log(value)


def _number_log(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Log(number, optional base) - default base is Number.E
    # (verified against docs: Number.Log(2) == Number.Log(2, 10) is NOT
    # true; the no-base example gives ln(2)).
    _arity("Number.Log", args, 1, 2)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    base = (
        _require_number(args[1]) if len(args) == 2 and args[1] is not None else math.e
    )
    if value <= 0 or base <= 0 or base == 1:
        return math.nan
    return math.log(value, base)


def _number_log10(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Log10", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    if value <= 0:
        return math.nan
    return math.log10(value)


def _number_sign(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Sign", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _round_scaled(
    value: int | float, digits: int, rounder: Callable[[int | float], int]
) -> float:
    # `10**digits` types as Any in typeshed (int.__pow__ can't statically
    # prove the sign of a non-literal exponent won't flip int->float) -
    # the explicit annotation stops that Any from leaking into the return.
    factor: int = 10**digits
    return float(rounder(value * factor)) / factor


def _number_round_up(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.RoundUp", args, 1, 2)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    digits = _require_int(args[1]) if len(args) == 2 and args[1] is not None else 0
    return _round_scaled(value, digits, math.ceil)


def _number_round_down(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.RoundDown", args, 1, 2)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    digits = _require_int(args[1]) if len(args) == 2 and args[1] is not None else 0
    return _round_scaled(value, digits, math.floor)


def _number_round_away_from_zero(args: list[Any], ctx: _Ctx) -> Any:
    # Trap (verified): NOT "round half away from zero" - every fraction
    # (not just ties) rounds away from zero. Number.RoundAwayFromZero(1.2)
    # is 2, not 1. Equivalent to RoundUp for >=0, RoundDown for <0.
    _arity("Number.RoundAwayFromZero", args, 1, 2)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    digits = _require_int(args[1]) if len(args) == 2 and args[1] is not None else 0
    rounder = math.ceil if value >= 0 else math.floor
    return _round_scaled(value, digits, rounder)


def _number_round_toward_zero(args: list[Any], ctx: _Ctx) -> Any:
    # Trap (verified): truncation toward zero for EVERY fraction.
    # Number.RoundTowardZero(-1.2) is -1, not -2.
    _arity("Number.RoundTowardZero", args, 1, 2)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    digits = _require_int(args[1]) if len(args) == 2 and args[1] is not None else 0
    return _round_scaled(value, digits, math.trunc)


def _number_to_text(args: list[Any], ctx: _Ctx) -> Any:
    # Number.ToText(number, optional format, optional culture). Full
    # .NET-style custom/standard numeric format strings are a large
    # surface; only the no-format default and the common "F<n>" fixed-
    # decimal format are implemented. Anything else raises UnsupportedError
    # naming the format rather than approximating it.
    _arity("Number.ToText", args, 1, 3)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("Number.ToText: culture argument")
    if len(args) >= 2 and args[1] is not None:
        fmt = _require_str(args[1])
        if fmt[:1] in ("F", "f") and (fmt[1:] == "" or fmt[1:].isdigit()):
            precision = int(fmt[1:]) if fmt[1:] else 2
            return f"{value:.{precision}f}"
        raise UnsupportedError(f"Number.ToText: format {fmt!r}")
    return _format_number(value)


def _number_is_nan(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.IsNaN", args, 1)
    value = _require_number(args[0])
    return isinstance(value, float) and math.isnan(value)


def _number_is_even(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.IsEven", args, 1)
    value = args[0]
    if value is None:
        return None
    return _require_int(value) % 2 == 0


def _number_is_odd(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.IsOdd", args, 1)
    value = args[0]
    if value is None:
        return None
    return _require_int(value) % 2 != 0


def _number_bitwise_and(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.BitwiseAnd", args, 2)
    a, b = args[0], args[1]
    if a is None or b is None:
        return None
    return _require_int(a) & _require_int(b)


def _number_bitwise_or(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.BitwiseOr", args, 2)
    a, b = args[0], args[1]
    if a is None or b is None:
        return None
    return _require_int(a) | _require_int(b)


def _number_bitwise_xor(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.BitwiseXor", args, 2)
    a, b = args[0], args[1]
    if a is None or b is None:
        return None
    return _require_int(a) ^ _require_int(b)


def _number_factorial(args: list[Any], ctx: _Ctx) -> Any:
    # Charges one budget step per multiplication (rule 6: no unbounded
    # loop over a caller-supplied count) so Number.Factorial(10**9) fails
    # fast with EvalError instead of burning CPU/memory on a number with
    # hundreds of millions of digits.
    _arity("Number.Factorial", args, 1)
    value = args[0]
    if value is None:
        return None
    n = _require_int(value)
    if n < 0:
        raise EvalError("Number.Factorial: number must not be negative")
    result = 1
    for i in range(2, n + 1):
        ctx.budget.tick()
        result *= i
    return result


def _number_random(args: list[Any], ctx: _Ctx) -> Any:
    # Non-deterministic by definition - callers must not assert on the
    # exact value, only that it lands in [0, 1).
    _arity("Number.Random", args, 0)
    return _random.random()


def _number_random_between(args: list[Any], ctx: _Ctx) -> Any:
    # Non-deterministic by definition - callers must not assert on the
    # exact value, only that it lands in [bottom, top).
    _arity("Number.RandomBetween", args, 2)
    bottom = _require_number(args[0])
    top = _require_number(args[1])
    if bottom > top:
        raise EvalError("Number.RandomBetween: bottom must not exceed top")
    return _random.uniform(bottom, top)


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
    "Number.IntegerDivide": _number_integer_divide,
    "Number.Mod": _number_mod,
    "Number.Power": _number_power,
    "Number.Sqrt": _number_sqrt,
    "Number.Exp": _number_exp,
    "Number.Ln": _number_ln,
    "Number.Log": _number_log,
    "Number.Log10": _number_log10,
    "Number.Sign": _number_sign,
    "Number.RoundUp": _number_round_up,
    "Number.RoundDown": _number_round_down,
    "Number.RoundAwayFromZero": _number_round_away_from_zero,
    "Number.RoundTowardZero": _number_round_toward_zero,
    "Number.ToText": _number_to_text,
    "Number.IsNaN": _number_is_nan,
    "Number.IsEven": _number_is_even,
    "Number.IsOdd": _number_is_odd,
    "Number.BitwiseAnd": _number_bitwise_and,
    "Number.BitwiseOr": _number_bitwise_or,
    "Number.BitwiseXor": _number_bitwise_xor,
    "Number.Factorial": _number_factorial,
    "Number.Random": _number_random,
    "Number.RandomBetween": _number_random_between,
}
