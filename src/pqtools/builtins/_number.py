"""``Number.*``, ``Logical.*``, and ``Json.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.
"""

from __future__ import annotations

import datetime
import decimal
import json as _json
import math
import random as _random
import re
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _check_invariant_culture,
    _format_number,
    _from_text,
    _parse_numeric_literal,
    _require_int,
    _require_number,
    _require_str,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx

# Culture handling for Number.ToText - mirrors _datetime.py's convention
# exactly (same set, same "null culture behaves like en-US" rule) rather
# than inventing a separate policy for numbers: `null` and an en-US-
# equivalent tag both render with the same (invariant-as-en-US) data this
# module hand-writes below; anything else names the culture and refuses.

# Precision.Double / Precision.Decimal enum members (Number.IntegerDivide's
# and Number.Mod's optional third argument). Verified against Power Query's
# own docs: Precision.Double = 0, Precision.Decimal = 1 - exposed as plain
# ints via BUILTINS, the same mechanism _datetime.py uses for Day.Sunday
# etc (see that module's BUILTINS comment for why this works uncalled).
_PRECISION_DOUBLE = 0
_PRECISION_DECIMAL = 1

# A standard .NET numeric format string is exactly one letter optionally
# followed by a precision-digit run (e.g. "F2", "N", "X4") - anything else
# (custom picture formats like "#,##0.00") is out of scope, per the task
# brief's "never approximate" rule, and falls through to the UnsupportedError
# in _number_to_text naming the format verbatim.
_STANDARD_FORMAT_RE = re.compile(r"^([A-Za-z])(\d*)$")

# In real .NET the case of a standard format specifier changes exactly one
# thing and only for E, G and X: which case the exponent letter (or the
# hex digits) is rendered in. Everything else about the rendering is
# identical. This module used to refuse the lowercase e/g/x outright,
# calling the difference "a second, unverified rendering path" - but
# Number.ToText's own example 2 states `Number.ToText(4, "e")` is
# "4.000000e+000", the "E" output with one character lowered, so the
# refusal was rejecting a documented call. The case is now carried through
# to the three renderers that can emit a letter.
_CASE_INSENSITIVE_FORMAT_LETTERS = frozenset("CDFNP")
_CASE_SENSITIVE_FORMAT_LETTERS = frozenset("EGX")


def _consume_budget(ctx: _Ctx, count: int) -> None:
    """Charge `count` steps against ctx.budget before an operation whose
    cost scales with a caller-supplied count (e.g. Number.Factorial), so a
    huge count fails fast with EvalError instead of hanging the process.
    See PRD-0.5.0-builtins.md correctness rule 6.
    """
    for _ in range(count):
        ctx.budget.tick()


def _ole_serial_from_naive_datetime(name: str, dt: datetime.datetime) -> float:
    """The forward half of ``_datetime.py``'s OLE Automation Date machinery.

    That module implements number -> date/datetime (``Date.From``,
    ``DateTime.From``) with a documented, deliberate simplification: the
    naive "epoch + N days" formula, exact for every real calendar date
    outside a 60-day 1900 phantom-leap-day window, which it refuses by name
    (``_reject_phantom_ole_window``) rather than guess at bug-compatible
    behaviour for. This is that same formula run backwards, against the
    SAME epoch constant and the SAME window check (imported, never
    reimplemented) - so ``Number.From(Date.From(n)) == n`` for every ``n``
    outside that window by construction, not by two independent pieces of
    arithmetic agreeing by luck. ``dt`` must already be naive (any
    datetimezone offset stripped by the caller - see the datetimezone
    branch of ``_number_from`` for why that is correct rather than a
    shortcut).
    """
    from ._datetime import _OLE_EPOCH, _reject_phantom_ole_window

    epoch = datetime.datetime(_OLE_EPOCH.year, _OLE_EPOCH.month, _OLE_EPOCH.day)
    delta = dt - epoch
    whole_days = delta.days
    _reject_phantom_ole_window(name, whole_days, whole_days)
    fractional_seconds = delta.seconds + delta.microseconds / 1_000_000
    return whole_days + fractional_seconds / 86400


def _number_from_text_value(text: str) -> Any:
    """`Number.From("4")`, and `Number.From("12.3%")` -> 0.123.

    The percent form is example 3 on the page, with that stated output. It
    raised "not a number" here, which is a conversion a real column of
    scraped or pasted percentages hits immediately.
    """
    stripped = text.strip()
    percent = stripped.endswith("%")
    if percent:
        stripped = stripped[:-1].rstrip()
    try:
        number = _parse_numeric_literal(stripped)
    except ValueError as error:
        raise EvalError(f"Number.From: not a number: {text!r}") from error
    if not percent:
        return number
    # Shift the decimal point in DECIMAL, not by dividing the binary
    # double: `12.3 / 100` is 0.12300000000000001, while the page states
    # 0.123. The two are different doubles, and the one the docs name is
    # the one a reader gets from typing 0.123 - so the digits are moved
    # before the value ever becomes a float.
    return float(decimal.Decimal(str(number)).scaleb(-2))


def _number_from(args: list[Any], ctx: _Ctx) -> Any:
    # Number.From(value as any, optional culture as nullable text) as
    # nullable number. The temporal branches below are grounded in
    # Number.From's own About text (verbatim, per conversion):
    #   datetime/date:  "a double-precision floating-point number that
    #                    contains an OLE Automation date equivalent"
    #   datetimezone:   "... an OLE Automation date equivalent of the
    #                    LOCAL date and time of value"
    #   time:           "Expressed in fractional days"
    #   duration:       "Expressed in whole and fractional days"
    # The `culture` parameter the Syntax block also documents is a
    # pre-existing, separate gap this task does not touch - every existing
    # call site here already assumes 1 argument, and none of the four
    # conversions above is culture-sensitive (an OLE serial has no locale).
    _arity("Number.From", args, 1)
    value = args[0]
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _number_from_text_value(value)
    if isinstance(value, datetime.datetime):
        # datetime/datetimezone - checked before `datetime.date` below
        # because datetime.datetime IS a datetime.date subclass (the
        # ordering trap _shared._type_name/_m_equal already document).
        if value.tzinfo is not None:
            # "the LOCAL date and time of value" (About, verbatim) means
            # the wall-clock fields AS DISPLAYED in the value's own
            # offset - not shifted to UTC, and not the host machine's zone
            # (this is not the HOST-dependent case module docstring point
            # 8 in _datetime.py calls out; the offset used is the one
            # already carried on the value, no lookup involved).
            # `.replace(tzinfo=None)` keeps every field except the offset
            # marker itself, which is exactly that.
            naive = value.replace(tzinfo=None)
        else:
            naive = value
        return _ole_serial_from_naive_datetime("Number.From", naive)
    if isinstance(value, datetime.date):
        naive = datetime.datetime(value.year, value.month, value.day)
        return _ole_serial_from_naive_datetime("Number.From", naive)
    if isinstance(value, datetime.time):
        # "Expressed in fractional days" (About, verbatim). A time has no
        # date component, so there is no OLE epoch or phantom-window
        # question here at all - just a seconds-in-a-day unit conversion,
        # the forward direction of _datetime.py's own
        # `_time_from_day_fraction`.
        seconds = (
            value.hour * 3600
            + value.minute * 60
            + value.second
            + value.microsecond / 1_000_000
        )
        return seconds / 86400
    if isinstance(value, datetime.timedelta):
        # "Expressed in whole and fractional days" (About, verbatim) -
        # reuses Duration.TotalDays's own arithmetic
        # (`td.total_seconds() / 86400`) rather than a second copy of it.
        from ._datetime import _duration_total_days

        return _duration_total_days([value], ctx)
    raise EvalError(f"Number.From: unsupported value type: {_type_name(value)}")


# RoundingMode.Type, by its documented number. The mode decides the TIE only:
# 1.4 rounds to 1 under every one of them.
_ROUND_UP, _ROUND_DOWN, _ROUND_AWAY, _ROUND_TOWARD, _ROUND_EVEN = 0, 1, 2, 3, 4


def _number_round(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Round", args, 1, 3)
    value = _require_number(args[0])
    digits = _require_int(args[1]) if len(args) >= 2 and args[1] is not None else 0
    if len(args) < 3 or args[2] is None:
        # Python's round() is round-half-to-even, which is also M's default:
        # Number.Round(1.5) and Number.Round(2.5) are both 2.
        return round(value, digits)
    mode = _require_int(args[2])
    if mode not in (_ROUND_UP, _ROUND_DOWN, _ROUND_AWAY, _ROUND_TOWARD, _ROUND_EVEN):
        raise EvalError(
            f"Number.Round: {mode} is not a RoundingMode "
            "(RoundingMode.Up/Down/AwayFromZero/TowardZero/ToEven)"
        )
    if mode == _ROUND_EVEN:
        return round(value, digits)
    scale = 10.0**digits
    scaled = value * scale
    floor = math.floor(scaled)
    remainder = scaled - floor
    if remainder != 0.5:
        # Not a tie, so the mode does not apply and ordinary rounding stands.
        return round(value, digits)
    if mode == _ROUND_UP:
        chosen = floor + 1
    elif mode == _ROUND_DOWN:
        chosen = floor
    elif mode == _ROUND_AWAY:
        chosen = floor + 1 if scaled > 0 else floor
    else:
        chosen = floor if scaled > 0 else floor + 1
    result = chosen / scale
    return result if digits > 0 else int(result)


def _number_abs(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Abs", args, 1)
    return abs(_require_number(args[0]))


def _json_document(args: list[Any], ctx: _Ctx) -> Any:
    # Accepts binary as well as text: the "Enter Data" shape Power BI writes
    # is Json.Document(Binary.Decompress(...)), so the argument arrives as
    # bytes far more often than as a string.
    _arity("Json.Document", args, 1, 2)
    raw = args[0]
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise EvalError(f"Json.Document: {error}") from error
    else:
        text = _require_str(raw)
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


def _resolve_precision(name: str, value: Any) -> int:
    # `null` (argument omitted or explicitly null) means Precision.Double -
    # verified: Value.Divide's docs state "by default Precision.Double is
    # used", and Number.IntegerDivide/Number.Mod share that same optional
    # precision parameter. `_require_int` already rejects bool, matching
    # every other enum-style argument check in this codebase.
    if value is None:
        return _PRECISION_DOUBLE
    resolved = _require_int(value)
    if resolved not in (_PRECISION_DOUBLE, _PRECISION_DECIMAL):
        raise EvalError(
            f"{name}: precision must be Precision.Double or Precision.Decimal"
        )
    return resolved


def _decimal_truncate_divide(number1: int | float, number2: int | float) -> int:
    # `str(x)` recovers the shortest decimal text that round-trips to the
    # Python float `x` (Python 3's float repr already computes that), which
    # is the only faithful way to hand a *decimal* value to `decimal.Decimal`
    # from a value this evaluator has already stored as a binary float -
    # `Decimal(0.1)` instead would just spell out 0.1's binary imprecision
    # in base 10, defeating the entire point of Precision.Decimal.
    d1 = decimal.Decimal(str(number1))
    d2 = decimal.Decimal(str(number2))
    quotient = (d1 / d2).to_integral_value(rounding=decimal.ROUND_DOWN)
    return int(quotient)


def _decimal_truncate_mod(number: int | float, divisor: int | float) -> float:
    d1 = decimal.Decimal(str(number))
    d2 = decimal.Decimal(str(divisor))
    quotient = (d1 / d2).to_integral_value(rounding=decimal.ROUND_DOWN)
    return float(d1 - quotient * d2)


def _number_integer_divide(args: list[Any], ctx: _Ctx) -> Any:
    # Number.IntegerDivide(number1, number2, optional precision) - integer
    # portion of number1/number2, TRUNCATED toward zero (verified: 8.3/3 =
    # 2, matching Python's math.trunc, not floor division).
    _arity("Number.IntegerDivide", args, 2, 3)
    precision = _resolve_precision(
        "Number.IntegerDivide", args[2] if len(args) == 3 else None
    )
    number1, number2 = args[0], args[1]
    if number1 is None or number2 is None:
        return None
    number1 = _require_number(number1)
    number2 = _require_number(number2)
    if number2 == 0:
        raise EvalError("Number.IntegerDivide: division by zero")
    if precision == _PRECISION_DECIMAL:
        return _decimal_truncate_divide(number1, number2)
    return math.trunc(number1 / number2)


def _number_mod(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Mod(number, divisor, optional precision) - trap (verified):
    # this is TRUNCATED (C-style) modulo, not Python's floored `%`.
    # Number.Mod(-7, 3) is -1 in real PQ, NOT the 2 that -7 % 3 gives in
    # Python. math.fmod matches PQ's truncation convention exactly.
    _arity("Number.Mod", args, 2, 3)
    precision = _resolve_precision("Number.Mod", args[2] if len(args) == 3 else None)
    number, divisor = args[0], args[1]
    if number is None or divisor is None:
        return None
    number = _require_number(number)
    divisor = _require_number(divisor)
    if divisor == 0:
        raise EvalError("Number.Mod: division by zero")
    if precision == _PRECISION_DECIMAL:
        result = _decimal_truncate_mod(number, divisor)
    else:
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


def _check_number_culture(name: str, culture: Any) -> None:
    # `null` passes (treated as en-US, per this module's docstring note
    # above); any other tag must name en-US/en. Shared with _datetime.py and
    # _type.py - it was copied into two modules before, which is how a rule
    # like this drifts.
    _check_invariant_culture(name, culture, "culture-specific number formatting")


def _require_whole_number(name: str, letter: str, value: int | float) -> int:
    # "D" and "X" are documented as integral-types-only in real .NET
    # (a fractional Double raises a FormatException there too) - this is a
    # genuine data/format mismatch, not a missing feature, so it is an
    # EvalError rather than an UnsupportedError.
    if isinstance(value, float) and not value.is_integer():
        raise EvalError(
            f"{name}: format {letter!r} requires a whole number, got {value!r}"
        )
    return int(value)


def _render_decimal_format(name: str, value: int | float, precision: int | None) -> str:
    n = _require_whole_number(name, "D", value)
    negative = n < 0
    digits = str(abs(n))
    if precision is not None and len(digits) < precision:
        digits = digits.zfill(precision)
    return f"-{digits}" if negative else digits


def _render_fixed_format(value: int | float, precision: int | None) -> str:
    return f"{value:.{2 if precision is None else precision}f}"


def _render_grouped_format(value: int | float, precision: int | None) -> str:
    return f"{value:,.{2 if precision is None else precision}f}"


def _render_scientific_format(
    value: int | float, precision: int | None, upper: bool = True
) -> str:
    # .NET's "E" always uses a MINIMUM of 3 exponent digits (verified
    # against the docs' own worked example: 1052.0329112756 ("E", en-US)
    # -> "1.052033E+003"). Python's own `E` presentation type only pads to
    # 2, so the exponent is re-padded by hand after formatting.
    p = 6 if precision is None else precision
    text = f"{float(value):.{p}E}"
    mantissa, exp_part = text.split("E")
    sign, digits = exp_part[0], exp_part[1:]
    return f"{mantissa}{'E' if upper else 'e'}{sign}{digits.zfill(3)}"


def _render_percent_format(value: int | float, precision: int | None) -> str:
    p = 2 if precision is None else precision
    return f"{value * 100:,.{p}f} %"


def _render_currency_format(value: int | float, precision: int | None) -> str:
    # en-US's default CurrencyNegativePattern wraps negative amounts in
    # parentheses rather than a leading minus sign - verified against the
    # docs' own worked example: -123.456 ("C3", en-US) -> "($123.456)".
    p = 2 if precision is None else precision
    negative = value < 0
    body = f"${abs(value):,.{p}f}"
    return f"({body})" if negative else body


def _render_hex_format(
    name: str, value: int | float, precision: int | None, upper: bool = True
) -> str:
    n = _require_whole_number(name, "X", value)
    if n < 0:
        # Real .NET two's-complements a negative integral value to its
        # declared bit width (sbyte/short/int/long all give a DIFFERENT
        # hex string for -1). pqtools' M numbers carry no such width, so
        # there is no faithful choice here - refuse by name rather than
        # picking one arbitrarily.
        raise UnsupportedError(
            f"{name}: format 'X' on a negative number (the two's-complement "
            "bit width is ambiguous for pqtools' arbitrary-precision numbers)"
        )
    digits = format(n, "X" if upper else "x")
    if precision is not None and len(digits) < precision:
        digits = digits.zfill(precision)
    return digits


def _general_digits_and_exponent(
    magnitude: float, precision: int | None
) -> tuple[str, int]:
    """Significant digits (as text) and the base-10 scientific exponent.

    `precision=None` uses the shortest decimal string that round-trips back
    to `magnitude` - Python's own `repr()` for a float already computes
    exactly that, the same guarantee .NET Core 3.0+'s Double formatting
    makes for a bare "G" (see the docstring note in _number_to_text).
    """
    base = decimal.Decimal(repr(magnitude))
    if precision is not None:
        with decimal.localcontext() as ctx:
            ctx.prec = max(precision, 1)
            ctx.rounding = decimal.ROUND_HALF_EVEN
            base = +base
    _sign, digit_tuple, exponent = base.as_tuple()
    assert isinstance(exponent, int)  # `magnitude` is always finite here
    digits = list(digit_tuple)
    # "trailing zeros after the decimal point are omitted" (docs) - true of
    # both the fixed and scientific renderings, so strip them once, here,
    # on the shared digit sequence rather than in each render branch.
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return "".join(str(d) for d in digits), len(digits) - 1 + exponent


def _render_general_format(
    value: int | float, precision: int | None, upper: bool = True
) -> str:
    # General ("G") format specifier - see the docs section quoted in the
    # module notes: fixed-point when the scientific exponent is in
    # (-5, precision), otherwise scientific with a MINIMUM of 2 exponent
    # digits (not 3, unlike "E" - verified, this is a documented difference
    # between the two format specifiers).
    if value == 0:
        return "0"
    negative = value < 0
    digit_str, exponent = _general_digits_and_exponent(abs(float(value)), precision)
    p = len(digit_str) if precision is None else precision
    if -5 < exponent < p:
        if exponent >= 0:
            int_len = exponent + 1
            if len(digit_str) <= int_len:
                text = digit_str.ljust(int_len, "0")
            else:
                text = f"{digit_str[:int_len]}.{digit_str[int_len:]}"
        else:
            text = f"0.{'0' * (-exponent - 1)}{digit_str}"
    else:
        mantissa = (
            digit_str if len(digit_str) == 1 else f"{digit_str[0]}.{digit_str[1:]}"
        )
        sign = "+" if exponent >= 0 else "-"
        letter = "E" if upper else "e"
        text = f"{mantissa}{letter}{sign}{str(abs(exponent)).zfill(2)}"
    return f"-{text}" if negative else text


def _number_to_text(args: list[Any], ctx: _Ctx) -> Any:
    # Number.ToText(number, optional format, optional culture). `format` is
    # a .NET standard numeric format string (a single letter + optional
    # precision digits, e.g. "F2", "N", "X4") - the custom-picture surface
    # ("#,##0.00" and friends) is out of scope and still refuses by name.
    #
    # NaN/Infinity render the same way under every standard format in real
    # .NET (they come from NumberFormatInfo.NaNSymbol/*InfinitySymbol*, not
    # from the format specifier), so they short-circuit before any format
    # parsing - this reuses the exact strings the no-format path already
    # produces, rather than a second, separately-verified rendering.
    _arity("Number.ToText", args, 1, 3)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    if len(args) == 3:
        _check_number_culture("Number.ToText", args[2])
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return _format_number(value)
    if len(args) < 2 or args[1] is None:
        return _format_number(value)
    fmt = _require_str(args[1])
    match = _STANDARD_FORMAT_RE.match(fmt)
    if match is None:
        raise UnsupportedError(f"Number.ToText: format {fmt!r}")
    letter, digit_text = match.groups()
    precision = int(digit_text) if digit_text else None
    specifier = letter.upper()
    # The one thing the input case decides - see the note beside
    # _CASE_SENSITIVE_FORMAT_LETTERS.
    uppercase = letter.isupper()
    if specifier == "D":
        return _render_decimal_format("Number.ToText", value, precision)
    if specifier == "F":
        return _render_fixed_format(value, precision)
    if specifier == "N":
        return _render_grouped_format(value, precision)
    if specifier == "E":
        return _render_scientific_format(value, precision, uppercase)
    if specifier == "P":
        return _render_percent_format(value, precision)
    if specifier == "C":
        return _render_currency_format(value, precision)
    if specifier == "X":
        return _render_hex_format("Number.ToText", value, precision, uppercase)
    if specifier == "G":
        # "the precision specifier is omitted OR ZERO" both mean default -
        # unlike every other format above, where an explicit 0 is a real,
        # honoured precision (e.g. "F0" really does mean zero decimals).
        return _render_general_format(
            value, None if precision == 0 else precision, uppercase
        )
    raise UnsupportedError(f"Number.ToText: format {fmt!r}")


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


def _number_combinations(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Combinations(setSize as nullable number, combinationSize as
    # nullable number) as nullable number. Verified against the docs' own
    # worked example: Number.Combinations(5, 3) -> 10. `math.comb` returns
    # 0 (not an error) when combinationSize > setSize - cross-checked with
    # Python's own math.comb, and this is the standard nCr convention (a
    # combination size larger than the set has zero ways to choose), not a
    # guess.
    _arity("Number.Combinations", args, 2)
    set_size, combination_size = args[0], args[1]
    if set_size is None or combination_size is None:
        return None
    n = _require_int(set_size)
    k = _require_int(combination_size)
    try:
        return math.comb(n, k)
    except ValueError as error:
        raise EvalError(f"Number.Combinations: {error}") from error


def _number_permutations(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Permutations(setSize as nullable number, permutationSize as
    # nullable number) as nullable number. Verified against the docs' own
    # worked example: Number.Permutations(5, 3) -> 60. Mirrors
    # Number.Combinations above: `math.perm` gives 0 for
    # permutationSize > setSize rather than raising, cross-checked with
    # Python's own math.perm.
    _arity("Number.Permutations", args, 2)
    set_size, permutation_size = args[0], args[1]
    if set_size is None or permutation_size is None:
        return None
    n = _require_int(set_size)
    k = _require_int(permutation_size)
    try:
        return math.perm(n, k)
    except ValueError as error:
        raise EvalError(f"Number.Permutations: {error}") from error


def _number_acos(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Acos(number as nullable number) as nullable number. The docs
    # page gives no worked example and no domain-error case, but this
    # module already has an established, cross-checked pattern for exactly
    # this family ("impossible real result -> NaN", not an exception) on
    # Number.Sqrt (docs-verified) and Number.Ln (analogy) above - applied
    # here by the same analogy. .NET's Math.Acos returns NaN for |x| > 1;
    # Python's math.acos RAISES ValueError instead (cross-checked with
    # Python's own math module) - the divergence this module's task brief
    # calls out explicitly, handled the same way Sqrt/Ln already handle it.
    _arity("Number.Acos", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    try:
        return math.acos(value)
    except ValueError:
        return math.nan


def _number_asin(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Asin - same NaN-on-domain-error analogy as Number.Acos above;
    # Python's math.asin raises ValueError for |x| > 1 where .NET's
    # Math.Asin returns NaN.
    _arity("Number.Asin", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    try:
        return math.asin(value)
    except ValueError:
        return math.nan


def _number_atan(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Atan(number as nullable number) as nullable number - defined
    # for every real input in both .NET and Python (no domain restriction,
    # unlike Acos/Asin), so no divergence to bridge here.
    _arity("Number.Atan", args, 1)
    value = args[0]
    if value is None:
        return None
    return math.atan(_require_number(value))


def _number_atan2(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Atan2(y as nullable number, x as nullable number) as nullable
    # number - "the angle whose tangent is the quotient y/x". Python's
    # math.atan2(y, x) takes the SAME (y, x) argument order and follows the
    # same IEEE-754 atan2 convention .NET's Math.Atan2(y, x) implements
    # (cross-checked: both give atan2(0, 0) == 0.0 and atan2(0, -1) == pi,
    # not a domain error), so there is no divergence to bridge for this one.
    _arity("Number.Atan2", args, 2)
    y, x = args[0], args[1]
    if y is None or x is None:
        return None
    return math.atan2(_require_number(y), _require_number(x))


def _number_cos(args: list[Any], ctx: _Ctx) -> Any:
    # Verified against the docs' own worked examples: Number.Cos(0) -> 1,
    # and Number.Cos(pi) -> -1 (checked here against Python's own
    # math.pi/math.cos rather than the unregistered M identifier
    # Number.PI - see the module note on Number.PI/Number.E below).
    _arity("Number.Cos", args, 1)
    value = args[0]
    if value is None:
        return None
    return math.cos(_require_number(value))


def _number_sin(args: list[Any], ctx: _Ctx) -> Any:
    # Verified against the docs' own worked example: Number.Sin(0) -> 0.
    _arity("Number.Sin", args, 1)
    value = args[0]
    if value is None:
        return None
    return math.sin(_require_number(value))


def _number_tan(args: list[Any], ctx: _Ctx) -> Any:
    # Verified against the docs' own worked example:
    # Number.Tan(1) -> 1.5574077246549023 (cross-checked with Python's own
    # math.tan(1), byte-for-byte identical - both are IEEE-754 doubles).
    _arity("Number.Tan", args, 1)
    value = args[0]
    if value is None:
        return None
    return math.tan(_require_number(value))


def _number_cosh(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Cosh - no domain restriction, but a large-magnitude input
    # OVERFLOWS a Python float in a way .NET's Math.Cosh does not: .NET
    # returns +Infinity, Python's math.cosh RAISES OverflowError (cross-
    # checked: math.cosh(1000) raises; the finite range ends well under
    # 1000). cosh is always >= 1 for every real input, so the overflow
    # direction is unconditionally +Infinity - mirrors this same module's
    # existing Number.Exp overflow handling above.
    _arity("Number.Cosh", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    try:
        return math.cosh(value)
    except OverflowError:
        return math.inf


def _number_sinh(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Sinh - same float-overflow divergence as Number.Cosh above
    # (cross-checked: math.sinh(1000) and math.sinh(-1000) both raise
    # OverflowError in Python where .NET's Math.Sinh returns +/-Infinity).
    # Unlike Cosh, sinh is sign-preserving, so the overflow direction
    # follows the sign of the input.
    _arity("Number.Sinh", args, 1)
    value = args[0]
    if value is None:
        return None
    value = _require_number(value)
    try:
        return math.sinh(value)
    except OverflowError:
        return math.inf if value > 0 else -math.inf


def _number_tanh(args: list[Any], ctx: _Ctx) -> Any:
    # Number.Tanh - bounded to (-1, 1) for every finite input and does not
    # overflow in Python (cross-checked: math.tanh(1000) == 1.0, no
    # exception), so no divergence to bridge here.
    _arity("Number.Tanh", args, 1)
    value = args[0]
    if value is None:
        return None
    return math.tanh(_require_number(value))


def _number_bitwise_not(args: list[Any], ctx: _Ctx) -> Any:
    # Number.BitwiseNot(number as any) as any - no worked example on the
    # docs page. `~x == -x - 1` is the two's-complement NOT identity and is
    # WIDTH-INDEPENDENT (unlike Number.ToText's "X" hex format above, whose
    # rendering genuinely depends on which fixed-width integral subtype is
    # in play - see _render_hex_format's refusal for a negative number).
    # Python's `~` operator already computes exactly this identity, so it
    # matches .NET's bitwise NOT for every value this evaluator can
    # represent, without needing to pick a bit width.
    _arity("Number.BitwiseNot", args, 1)
    value = args[0]
    if value is None:
        return None
    return ~_require_int(value)


def _number_bitwise_shift_left(args: list[Any], ctx: _Ctx) -> Any:
    # Number.BitwiseShiftLeft(number1, number2) - shifts number1 left by
    # number2 bits. For a non-negative shift this is exactly
    # `number1 * 2**number2`, width-independent and identical in .NET and
    # Python. A NEGATIVE shift count is refused: real .NET masks it to its
    # low 6 bits (mod 64) because Int64 has a fixed 64-bit width, and this
    # evaluator's arbitrary-precision numbers have no faithful equivalent
    # to mask against (the same reason _render_hex_format refuses "X" on a
    # negative number above) - not a documented worked example, but the
    # same width-ambiguity refusal already established in this codebase.
    _arity("Number.BitwiseShiftLeft", args, 2)
    number1, number2 = args[0], args[1]
    if number1 is None or number2 is None:
        return None
    value = _require_int(number1)
    shift = _require_int(number2)
    if shift < 0:
        raise UnsupportedError(
            "Number.BitwiseShiftLeft: negative shift amount (the 64-bit "
            "wraparound this needs is ambiguous for pqtools' "
            "arbitrary-precision numbers)"
        )
    return value << shift


def _number_bitwise_shift_right(args: list[Any], ctx: _Ctx) -> Any:
    # Number.BitwiseShiftRight - mirror of BitwiseShiftLeft above. Python's
    # `>>` on a (possibly negative) int is an ARITHMETIC shift (sign-
    # preserving, equivalent to floor division by 2**shift), matching
    # .NET's `>>` on a signed integer exactly for a non-negative shift
    # count. Negative shift amounts are refused for the same
    # fixed-64-bit-width reason as BitwiseShiftLeft.
    _arity("Number.BitwiseShiftRight", args, 2)
    number1, number2 = args[0], args[1]
    if number1 is None or number2 is None:
        return None
    value = _require_int(number1)
    shift = _require_int(number2)
    if shift < 0:
        raise UnsupportedError(
            "Number.BitwiseShiftRight: negative shift amount (the 64-bit "
            "wraparound this needs is ambiguous for pqtools' "
            "arbitrary-precision numbers)"
        )
    return value >> shift


def _logical_to_text(args: list[Any], ctx: _Ctx) -> Any:
    # Logical.ToText(logicalValue as nullable logical) as nullable text.
    # Verified against the docs' own worked example: Logical.ToText(true)
    # -> "true" (LOWERCASE). This module's own Text.From(true) already
    # produces "TRUE" (uppercase, in _text.py) - two different functions
    # with two different documented castings for the same input, not a
    # contradiction to reconcile.
    _arity("Logical.ToText", args, 1)
    value = args[0]
    if value is None:
        return None
    if not isinstance(value, bool):
        raise EvalError(
            f"Logical.ToText: expected a logical value, got {_type_name(value)}"
        )
    return "true" if value else "false"


# --------------------------------------------------------------------------
# Byte.From / Currency.From / Decimal.From / Double.From / Int8.From /
# Int16.From / Int32.From / Int64.From / Percentage.From / Single.From /
# Value.FromText - the numeric X.From conversions. Verified against each
# function's own learn.microsoft.com/en-us/powerquery-m/<name> page - there
# is no separate byte-type/int8-type/int16-type/... reference page (those
# slugs are all live 404s, checked directly), so the RANGE each function
# enforces is the corresponding .NET primitive's well-known range, computed
# below from a formula rather than typed as a literal digit string, which
# is exactly the kind of transcription slip a range check would otherwise
# hide silently.
# --------------------------------------------------------------------------

_BYTE_MIN, _BYTE_MAX = 0, 2**8 - 1
_INT8_MIN, _INT8_MAX = -(2**7), 2**7 - 1
_INT16_MIN, _INT16_MAX = -(2**15), 2**15 - 1
_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1

# Currency's own page states this range verbatim, and it is exactly Int64's
# range scaled by the type's fixed 4 decimal digits (9223372036854775807 /
# 10000 = 922337203685477.5807) - copied from the fetched page as a literal
# rather than computed, since a plain float division here would need
# decimal.Decimal to land on the exact printed digits, for a value this
# evaluator's float-based number model cannot hold any more precisely than
# the literal already does anyway (see this task's report: doubles cannot
# represent 4 decimal digits exactly at Currency's full 15-digit magnitude).
_CURRENCY_MIN, _CURRENCY_MAX = -922337203685477.5808, 922337203685477.5807
# The literal floats above are already the nearest double to each bound (a
# double cannot resolve 4 decimal digits at this ~15-digit magnitude - the
# gap between adjacent doubles here is roughly 0.1, well past ".5807"), so
# an f-string of `_CURRENCY_MAX` itself would print something like
# "922337203685477.6" in an error message - technically the same double,
# but not the range the docs actually state. This text constant is for
# error messages only; the float pair above is still what every comparison
# uses.
_CURRENCY_RANGE_TEXT = "-922337203685477.5808 to 922337203685477.5807"

# IEEE-754 binary32 max, computed exactly: (2 - 2**-23) needs 24 significant
# bits, well inside a double's 52-bit mantissa, and multiplying it by 2**127
# is an exact exponent shift - no rounding anywhere in this formula, unlike
# typing "3.4028235E+38" by hand from memory.
_SINGLE_MAX = (2 - 2**-23) * 2**127
_DOUBLE_MAX = sys.float_info.max  # the exact IEEE-754 binary64 max
# .NET's System.Decimal is a 96-bit-mantissa fixed-point type; its
# documented MaxValue (79228162514264337593543950335) is exactly 2**96 - 1.
_DECIMAL_MAX = float(2**96 - 1)


def _resolve_rounding_mode(name: str, mode: Any) -> int | None:
    # Shared by every X.From below that takes a roundingMode argument
    # (Byte/Currency/Int8/16/32/64 - never Single/Double/Decimal, whose
    # Syntax blocks have no third parameter at all). `None` here means "use
    # Number.Round's own None-path", which is already round-half-to-even -
    # exactly RoundingMode.ToEven, the documented default for this whole
    # family, so there is no separate default branch to maintain.
    if mode is None:
        return None
    resolved = _require_int(mode)
    if resolved not in (
        _ROUND_UP,
        _ROUND_DOWN,
        _ROUND_AWAY,
        _ROUND_TOWARD,
        _ROUND_EVEN,
    ):
        raise EvalError(
            f"{name}: {resolved} is not a RoundingMode "
            "(RoundingMode.Up/Down/AwayFromZero/TowardZero/ToEven)"
        )
    return resolved


def _convert_via_number_from(name: str, value: Any, ctx: _Ctx) -> int | float:
    """Every function below falls back to this for a non-number argument.

    Byte/Currency/Int8/16/32/64/Single/Double/Decimal's own pages all say
    "converted to a number using Number.FromText"; Percentage.From's page
    says "using Number.From" instead. Number.FromText, in this codebase, IS
    Number.From with a text-only type check bolted on (see _from_text
    above) - the two delegates are the same function for every value that
    reaches this line, so one shared implementation honestly serves both
    worded descriptions rather than maintaining two delegates that happen
    to agree on every input this evaluator can construct.

    Renaming the error prefix mirrors _from_text's own rename above, for
    the same reason: a Byte.From caller should see "Byte.From: ...", not
    "Number.From: ...".
    """
    try:
        result: int | float = _number_from([value], ctx)
        return result
    except EvalError as error:
        message = str(error)
        prefix = "Number.From: "
        if message.startswith(prefix):
            message = f"{name}: {message[len(prefix) :]}"
        raise EvalError(message) from error


def _make_integer_from(
    name: str, minimum: int, maximum: int
) -> Callable[[list[Any], _Ctx], Any]:
    """Byte.From/Int8.From/Int16.From/Int32.From/Int64.From: identical
    Syntax blocks (value / optional culture / optional roundingMode) and
    identical About prose modulo the type name and its range. Rounds a
    fractional value to a whole number via Number.Round's own tie-break
    logic (RoundingMode.ToEven by default - see _resolve_rounding_mode),
    then range-checks the whole-number result.

    Neither page's About text spells out what happens OUT of range - the
    task brief settles it: Byte.From(300) and Int8.From(200) must error,
    not wrap, so this raises rather than silently truncating to the type's
    bit width the way an unchecked cast would.
    """

    def run(args: list[Any], ctx: _Ctx) -> Any:
        _arity(name, args, 1, 3)
        culture = args[1] if len(args) >= 2 else None
        _check_invariant_culture(name, culture, "culture-specific numeric parsing")
        mode = _resolve_rounding_mode(name, args[2] if len(args) == 3 else None)
        value = args[0]
        if value is None:
            return None
        number = _convert_via_number_from(name, value, ctx)
        if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
            # Neither NaN nor +/-Infinity has a whole-number value, and
            # Number.Round's own tie-break path raises an unguarded Python
            # OverflowError on infinity (`math.floor(inf)`, verified) rather
            # than a typed error - refusing here, before that call, is what
            # keeps this a typed EvalError instead of a leaked OverflowError.
            raise EvalError(
                f"{name}: {_format_number(number)} has no whole-number value"
            )
        rounded = int(_number_round([number, 0, mode], ctx))
        if not minimum <= rounded <= maximum:
            raise EvalError(
                f"{name}: {rounded} is outside the range {minimum} to {maximum}"
            )
        return rounded

    run.__name__ = f"_{name.replace('.', '_').lower()}"
    return run


def _make_ranged_from(
    name: str, minimum: float, maximum: float, allow_special: bool
) -> Callable[[list[Any], _Ctx], Any]:
    """Single.From/Double.From/Decimal.From: value / optional culture, no
    roundingMode parameter at all - their About text never mentions
    rounding, only a range check ("value is returned, otherwise an error is
    returned"). `allow_special` distinguishes the two IEEE-754 binary types
    (Single, Double), which have real NaN/Infinity values, from Decimal,
    .NET's exact fixed-point type, which has no representation for either.
    """

    def run(args: list[Any], ctx: _Ctx) -> Any:
        _arity(name, args, 1, 2)
        culture = args[1] if len(args) == 2 else None
        _check_invariant_culture(name, culture, "culture-specific numeric parsing")
        value = args[0]
        if value is None:
            return None
        number = _convert_via_number_from(name, value, ctx)
        if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
            if allow_special:
                return number
            raise EvalError(f"{name}: {_format_number(number)} is not representable")
        if not minimum <= number <= maximum:
            raise EvalError(
                f"{name}: {number} is outside the range {minimum} to {maximum}"
            )
        return number

    run.__name__ = f"_{name.replace('.', '_').lower()}"
    return run


_byte_from = _make_integer_from("Byte.From", _BYTE_MIN, _BYTE_MAX)
_int8_from = _make_integer_from("Int8.From", _INT8_MIN, _INT8_MAX)
_int16_from = _make_integer_from("Int16.From", _INT16_MIN, _INT16_MAX)
_int32_from = _make_integer_from("Int32.From", _INT32_MIN, _INT32_MAX)
_int64_from = _make_integer_from("Int64.From", _INT64_MIN, _INT64_MAX)
_double_from = _make_ranged_from(
    "Double.From", -_DOUBLE_MAX, _DOUBLE_MAX, allow_special=True
)
_single_from = _make_ranged_from(
    "Single.From", -_SINGLE_MAX, _SINGLE_MAX, allow_special=True
)
_decimal_from = _make_ranged_from(
    "Decimal.From", -_DECIMAL_MAX, _DECIMAL_MAX, allow_special=False
)


def _currency_from(args: list[Any], ctx: _Ctx) -> Any:
    # Currency.From: the one member of the rounding family that rounds to 4
    # decimal digits instead of a whole number - everything else about its
    # Syntax block matches Byte/Int8/16/32/64 exactly, but sharing
    # _make_integer_from would need a `digits` parameter threaded through a
    # helper whose name says "integer", so this stays its own function.
    _arity("Currency.From", args, 1, 3)
    culture = args[1] if len(args) >= 2 else None
    _check_invariant_culture(
        "Currency.From", culture, "culture-specific numeric parsing"
    )
    mode = _resolve_rounding_mode("Currency.From", args[2] if len(args) == 3 else None)
    value = args[0]
    if value is None:
        return None
    number = _convert_via_number_from("Currency.From", value, ctx)
    if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
        # Currency has no NaN/Infinity representation (it is .NET's fixed-
        # point scaled-Int64 type, not an IEEE-754 float) - same reasoning,
        # and the same unguarded OverflowError, as Decimal.From above.
        raise EvalError(f"Currency.From: {_format_number(number)} is not representable")
    rounded = _number_round([number, 4, mode], ctx)
    if not _CURRENCY_MIN <= rounded <= _CURRENCY_MAX:
        raise EvalError(
            f"Currency.From: {rounded} is outside the range {_CURRENCY_RANGE_TEXT}"
        )
    return rounded


def _percent_text_to_number(name: str, raw: str, numeric_text: str) -> float:
    """Shared by Percentage.From and Value.FromText's own "%"-suffix branch.

    Plain float division lands on the wrong side of the last bit here:
    `12.3 / 100 == 0.12300000000000001` (verified), not the nearest double
    to 0.123 that the page's own worked example prints. Going through
    `decimal.Decimal(str(x))` for the division - the exact pattern
    `_decimal_truncate_divide` above already uses, for the same reason - and
    back to float lands on the SAME correctly-rounded double `float("0.123")`
    would, matching the doc's printed digits instead of merely being close.
    """
    try:
        parsed = _parse_numeric_literal(numeric_text)
    except ValueError as error:
        raise EvalError(f"{name}: not a number: {raw!r}") from error
    return float(decimal.Decimal(str(parsed)) / decimal.Decimal(100))


def _percentage_from(args: list[Any], ctx: _Ctx) -> Any:
    # Percentage.From(value as any, optional culture as nullable text) - no
    # roundingMode parameter (unlike its integer-family neighbours above).
    # Verified against the page's own worked example: "12.3%" -> 0.123.
    _arity("Percentage.From", args, 1, 2)
    culture = args[1] if len(args) == 2 else None
    _check_invariant_culture(
        "Percentage.From", culture, "culture-specific numeric parsing"
    )
    value = args[0]
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.endswith("%"):
            return _percent_text_to_number(
                "Percentage.From", value, stripped[:-1].strip()
            )
    # "Otherwise, the value will be converted to a number using Number.From"
    # - the page's own wording, and genuinely different from every sibling
    # above (which all name Number.FromText instead): a bare number is NOT
    # divided by 100 here, only text with a trailing "%" is.
    return _convert_via_number_from("Percentage.From", value, ctx)


def _value_from_text(args: list[Any], ctx: _Ctx) -> Any:
    # Value.FromText(text as any, optional culture as nullable text) as any.
    # Verified against the page's own worked examples 1 and 2 (a plain
    # number, and a percentage). Examples 3 and 4 (a fr-FR currency amount,
    # a de-DE date/time) both use a non-invariant culture and are refused
    # by the culture check below before this function would need to guess
    # at locale-specific decimal separators or month names - pqtools only
    # implements invariant/en-US, the same rule every other culture
    # parameter in this module already enforces.
    _arity("Value.FromText", args, 1, 2)
    culture = args[1] if len(args) == 2 else None
    _check_invariant_culture(
        "Value.FromText", culture, "culture-specific text interpretation"
    )
    text = args[0]
    if text is None:
        return None
    if not isinstance(text, str):
        raise EvalError(f"Value.FromText: expected text, got {_type_name(text)}")
    if text == "":
        # "An empty text value is interpreted as a null value" - the page's
        # own words, checked against the text as given: whitespace-only
        # text is not documented as empty and falls through to the parse
        # attempts below instead.
        return None
    stripped = text.strip()
    if stripped.endswith("%"):
        return _percent_text_to_number("Value.FromText", text, stripped[:-1].strip())
    try:
        return _parse_numeric_literal(stripped)
    except ValueError:
        pass
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    # The page's own About text lists the return type as "number, logical,
    # null, datetime, duration, or text" - datetime/duration detection is
    # real, documented Value.FromText behaviour that this function does not
    # attempt: that parsing lives in _datetime.py, a file this task does
    # not own (see this task's report). Falling through to the "text"
    # branch of that same documented type union is a disclosed gap, not a
    # silent wrong answer - a genuine date string under an invariant
    # culture stays text here instead of becoming a datetime.
    return text


# Number.PI / Number.E are not registered: neither name appeared in the
# fetched Microsoft Learn Number-functions table this task's diff was built
# from (only Number.Log's default-base behaviour references Number.E, in a
# comment above), so adding them would be exactly the "write a function name
# from memory" this task's brief forbids.


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
BUILTINS: dict[str, Any] = {
    # Number.FromText(text, optional culture as nullable text) - a
    # culture string, not the [Format=..] record its date siblings take.
    "Number.FromText": _from_text("Number.FromText", _number_from, options="culture"),
    # Logical.FromText(text as nullable text) - one argument.
    "Logical.FromText": _from_text("Logical.FromText", _logical_from, options="none"),
    "Number.From": _number_from,
    # "A constant that represents 3.1415926535897932" - a value, not a
    # function, so the identifier resolver returns it directly.
    "Number.PI": math.pi,
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
    "Number.Combinations": _number_combinations,
    "Number.Permutations": _number_permutations,
    "Number.Acos": _number_acos,
    "Number.Asin": _number_asin,
    "Number.Atan": _number_atan,
    "Number.Atan2": _number_atan2,
    "Number.Cos": _number_cos,
    "Number.Sin": _number_sin,
    "Number.Tan": _number_tan,
    "Number.Cosh": _number_cosh,
    "Number.Sinh": _number_sinh,
    "Number.Tanh": _number_tanh,
    "Number.BitwiseNot": _number_bitwise_not,
    "Number.BitwiseShiftLeft": _number_bitwise_shift_left,
    "Number.BitwiseShiftRight": _number_bitwise_shift_right,
    "Logical.ToText": _logical_to_text,
    "Precision.Double": _PRECISION_DOUBLE,
    "Precision.Decimal": _PRECISION_DECIMAL,
    "Byte.From": _byte_from,
    "Currency.From": _currency_from,
    "Decimal.From": _decimal_from,
    "Double.From": _double_from,
    "Int8.From": _int8_from,
    "Int16.From": _int16_from,
    "Int32.From": _int32_from,
    "Int64.From": _int64_from,
    "Percentage.From": _percentage_from,
    "Single.From": _single_from,
    "Value.FromText": _value_from_text,
}
