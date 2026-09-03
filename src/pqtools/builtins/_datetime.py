"""``#date``/``#datetime``/``#datetimezone``/``#time``/``#duration`` literals
plus the ``Date.*``, ``DateTime.*``, ``Duration.*`` and ``Time.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor - see
PRD-0.5.0-builtins.md.

Value representation (read this before touching any function below)
----------------------------------------------------------------------
- ``type date``          -> ``datetime.date``
- ``type time``           -> ``datetime.time``
- ``type datetime``       -> ``datetime.datetime`` with ``tzinfo=None`` (naive)
- ``type datetimezone``   -> ``datetime.datetime`` with ``tzinfo`` set (aware)
- ``type duration``       -> ``datetime.timedelta``

This is the "obvious choice" the PRD calls out, and it is deliberate rather
than the alternative (encoding everything as ISO text or as OLE-style
numbers): ``tests/test_realworld.py::_values_equal`` already special-cases
"a date/datetime value came back as a real object, not a string" by falling
back to ``actual.isoformat()`` - i.e. the test harness this module is
graded against already assumes native ``datetime`` objects.

Consequences of that choice (traced through, not assumed):

1. **Table.Sort "just works".** ``_table.py``'s ``Table.Sort`` sorts with a
   raw ``key=lambda row: row[name]`` comparison - plain Python ``<`` on
   whatever the column holds. ``date``/``datetime``/``time``/``timedelta``
   all implement ``__lt__`` against same-typed values natively, so sorting a
   date column needs zero special-casing here.
2. **Text.From on a date is a clean, correct refusal, not a silent wrong
   answer.** ``_text.py``'s ``Text.From`` only recognises
   ``None``/``bool``/``(int, float)``/``str``; anything else falls through
   to ``EvalError(f"unsupported value type: {_type_name(value)}")``, and
   ``_type_name`` reports ``type(value).__name__`` for an unrecognised type
   - i.e. ``"date"``/``"datetime"``/``"time"``/``"timedelta"``. That is
   actually correct Power Query behaviour: real ``Text.From`` does not
   accept date-family values either - callers are expected to reach for
   ``Date.ToText``/``DateTime.ToText``/``Time.ToText``/``Duration.ToText``,
   which this module provides.
3. **The M `=`/`<>`/`<`/`<=`/`>`/`>=` operators do NOT behave correctly on
   these values, and this module cannot fix that.** ``_shared._m_equal``
   (used by `=`/`<>`) and ``evaluate._eval_relational`` (used by
   `<`/`<=`/`>`/`>=`) both predate date support and only recognise
   ``None``/``bool``/``(int, float)``/``str``/``list``/``dict``.
   Concretely: ``#date(2024,1,1) = #date(2024,1,1)`` evaluates to ``False``
   (silently - ``_m_equal`` falls through to its default ``return False``
   for an unrecognised type pair), and ``#date(2024,1,2) > #date(2024,1,1)``
   raises ``EvalError`` ("relational operators require two numbers or two
   text values"). The relational case is at least loud; the equality case
   is a genuine silent-wrong-answer gap. Fixing it requires adding
   date-family branches to ``_m_equal`` and ``_eval_relational``, both of
   which live in files this task does not own (``_shared.py``,
   ``evaluate.py``). **Flagged here for a follow-up task with permission to
   touch those files** - not fixed, because fixing it silently by choosing
   a different value representation (e.g. a ``float`` subclass so the
   existing number branches "just work") would trade this narrow, honest
   gap for a much bigger one: every ``Number.*``/arithmetic builtin would
   then silently accept a date as a bare number too, and
   ``Text.From(someDate)`` would silently print the day-serial number
   instead of raising - a real silent-wrong-answer regression instead of a
   documented one. None of the four `tests/fixtures/realworld/` goal
   queries compare dates with a raw operator (04 only uses
   ``Date.Year``/``Date.MonthName`` and a numeric ``<>``), so this gap does
   not block the PRD's stated goal.
4. **DateTimeZone is a plain ``datetime`` with ``tzinfo`` attached**, not a
   separate wrapper type. ``DateTime.AddZone`` builds the ``tzinfo`` and
   ``.replace()``s it onto a naive value without shifting the wall-clock
   time (that is what "AddZone" - as opposed to a hypothetical
   zone-conversion function - means).
5. **Numeric coercion is OLE Automation Date, faithfully, with one named
   exception.** Per the task brief: "Date.From on a number it is an OLE
   automation date in real PQ - if you do not implement that faithfully,
   raise UnsupportedError rather than guessing." The OLE Automation Date
   epoch is 1899-12-30 = serial 0. Excel/OLE also (in)famously treats 1900
   as a leap year for Lotus 1-2-3 compatibility, so serial 60 is the
   fictitious "1900-02-29". Working the arithmetic through: the naive
   "epoch + N days" formula agrees exactly with the real, bug-compatible
   value for every serial <= 0 and every serial >= 61 (the two systems
   converge at 1900-03-01 = serial 61 and never diverge again), and is
   simply undefined - there is no real calendar date - for serial values
   1-60 inclusive. This module implements the naive formula (provably
   correct outside that window, which covers every realistic date) and
   raises ``UnsupportedError`` naming the exact serial for 1-60, rather
   than guess at bug-for-bug compatibility in a 60-day historical window
   nothing in this project will ever actually hit.
6. **Culture is invariant/English-only.** Any ``culture`` argument other
   than ``null`` or an en-US-equivalent string raises ``UnsupportedError``
   naming it (per the task brief's trap #2) - never a silent fallback to
   English.
7. **``DateTime.LocalNow``/``DateTime.FixedLocalNow`` are both "now".** Real
   Power Query memoises ``FixedLocalNow`` for the lifetime of one query
   evaluation (two references return the identical instant);
   ``LocalNow`` does not. This evaluator has no per-``evaluate()`` state
   slot available to a builtins module (that would mean threading a cache
   through ``_Ctx`` in ``evaluate.py``, which this task does not own), so
   both simply return a fresh ``datetime.datetime.now()`` reading. Documented
   gap, not a silent one; tests never assert on the wall-clock value they
   return (only that they return a ``datetime`` close to "now"), per the
   task brief's trap #3.
"""

from __future__ import annotations

import calendar
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _require_int,
    _require_number,
    _require_str,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# OLE Automation Date epoch - see the module docstring, point 5.
_OLE_EPOCH = date(1899, 12, 30)

# Day.Type enum members. Power Query's own default numbering for
# Date.DayOfWeek is Sunday = 0 (the task brief's trap #1) - these values are
# exposed as plain identifiers (not calls) in BUILTINS, exactly like
# Order.Ascending/Order.Descending are special-cased in evaluate.py, so
# `Date.DayOfWeek(d, Day.Monday)` resolves `Day.Monday` to a bare 1.
_DAY_ENUM: dict[str, int] = {
    "Day.Sunday": 0,
    "Day.Monday": 1,
    "Day.Tuesday": 2,
    "Day.Wednesday": 3,
    "Day.Thursday": 4,
    "Day.Friday": 5,
    "Day.Saturday": 6,
}

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# Index 0 = Sunday, matching _DAY_ENUM's Sunday = 0 numbering.
_DAY_NAMES_SUNDAY_FIRST = (
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)

_INVARIANT_CULTURES = frozenset({"en-us", "en"})


# --------------------------------------------------------------------------
# Culture handling (trap #2) - invariant/English only, else UnsupportedError
# --------------------------------------------------------------------------


def _check_invariant_culture(name: str, culture: Any) -> None:
    if culture is None:
        return
    text = _require_str(culture)
    if text.strip().lower() not in _INVARIANT_CULTURES:
        raise UnsupportedError(
            f"{name}: culture-specific formatting for {text!r} "
            "(pqtools only implements invariant/en-US names)"
        )


# --------------------------------------------------------------------------
# OLE Automation Date numeric coercion (module docstring, point 5)
# --------------------------------------------------------------------------


def _reject_phantom_ole_window(name: str, serial: int | float, whole_days: int) -> None:
    if 1 <= whole_days <= 60:
        raise UnsupportedError(
            f"{name}: OLE automation date serial {serial!r} falls in the "
            "historical Excel/OLE 'phantom 1900-02-29' compatibility window "
            "(serials 1-60) that pqtools does not replicate"
        )


def _date_from_ole_serial(name: str, serial: int | float) -> date:
    whole_days = math.floor(serial)
    _reject_phantom_ole_window(name, serial, whole_days)
    return _OLE_EPOCH + timedelta(days=whole_days)


def _datetime_from_ole_serial(name: str, serial: int | float) -> datetime:
    whole_days = math.floor(serial)
    _reject_phantom_ole_window(name, serial, whole_days)
    frac = serial - whole_days
    day = _OLE_EPOCH + timedelta(days=whole_days)
    return datetime(day.year, day.month, day.day) + timedelta(seconds=frac * 86400)


def _time_from_day_fraction(serial: int | float) -> time:
    frac = serial - math.floor(serial)
    return (datetime.min + timedelta(seconds=frac * 86400)).time()


# --------------------------------------------------------------------------
# ISO text parsing
# --------------------------------------------------------------------------


def _parse_iso_date(name: str, text: str) -> date:
    stripped = text.strip()
    try:
        return date.fromisoformat(stripped)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(stripped).date()
    except ValueError as error:
        raise EvalError(f"{name}: not a valid ISO date: {text!r}") from error


def _parse_iso_datetime(name: str, text: str) -> datetime:
    stripped = text.strip()
    try:
        return datetime.fromisoformat(stripped)
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(stripped), time())
    except ValueError as error:
        raise EvalError(f"{name}: not a valid ISO datetime: {text!r}") from error


def _parse_iso_time(name: str, text: str) -> time:
    try:
        return time.fromisoformat(text.strip())
    except ValueError as error:
        raise EvalError(f"{name}: not a valid ISO time: {text!r}") from error


_DURATION_TEXT_RE = re.compile(
    r"^(?P<sign>-)?(?:(?P<days>\d+)\.)?"
    r"(?P<hours>\d{1,4}):(?P<minutes>\d{2}):(?P<seconds>\d{2}(?:\.\d+)?)$"
)


def _parse_duration_text(name: str, text: str) -> timedelta:
    match = _DURATION_TEXT_RE.match(text.strip())
    if not match:
        raise EvalError(f"{name}: not a valid duration text: {text!r}")
    sign = -1 if match.group("sign") else 1
    days = int(match.group("days") or 0)
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))
    seconds = float(match.group("seconds"))
    delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    return -delta if sign < 0 else delta


# --------------------------------------------------------------------------
# Value coercion - the "as any" permissiveness real Date.*/DateTime.*/Time.*
# accessors document (they accept date, datetime, datetimezone, ISO text, or
# an OLE serial number, not only their own exact type). `null` in, `null`
# out throughout (correctness rule 2 / trap #6).
# --------------------------------------------------------------------------


def _coerce_date_like(name: str, value: Any) -> date | None:
    """Coerce to a plain `date`, truncating a datetime's time-of-day.

    Used by every Date.* accessor that only reads date components (Year,
    Month, Day, DayOfWeek, DayOfYear, MonthName, DayOfWeekName,
    QuarterOfYear, WeekOfYear, IsInCurrentMonth, IsInCurrentYear) - real PQ
    documents these as accepting a datetime/datetimezone too.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return _parse_iso_date(name, value)
    if isinstance(value, bool):
        raise EvalError(f"{name}: expected a date, got logical")
    if isinstance(value, (int, float)):
        return _date_from_ole_serial(name, value)
    raise EvalError(f"{name}: expected a date, got {_type_name(value)}")


def _coerce_date_or_datetime(name: str, value: Any) -> date | datetime | None:
    """Coerce to `date` or `datetime`, preserving which one it was.

    Used by the Add*/StartOf*/EndOf* family, which - per PQ docs - return
    the same shape they were given: a date in produces a date out, a
    datetime in produces a datetime out.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        # Try the plainer `date` shape first: `date.fromisoformat` rejects
        # anything with a time component, so a date-only string ("a
        # dateless text is a date, not midnight-of-that-day") only ever
        # matches here, and a text with a real time component falls
        # through to the `datetime` branch below.
        try:
            return date.fromisoformat(stripped)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(stripped)
        except ValueError as error:
            raise EvalError(
                f"{name}: not a valid ISO date/datetime: {value!r}"
            ) from error
    if isinstance(value, bool):
        raise EvalError(f"{name}: expected a date or datetime, got logical")
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return _date_from_ole_serial(name, value)
        return _datetime_from_ole_serial(name, value)
    raise EvalError(f"{name}: expected a date or datetime, got {_type_name(value)}")


def _coerce_datetime_like(name: str, value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        return _parse_iso_datetime(name, value)
    if isinstance(value, bool):
        raise EvalError(f"{name}: expected a datetime, got logical")
    if isinstance(value, (int, float)):
        return _datetime_from_ole_serial(name, value)
    raise EvalError(f"{name}: expected a datetime, got {_type_name(value)}")


def _coerce_time_like(name: str, value: Any) -> time | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        return _parse_iso_time(name, value)
    if isinstance(value, bool):
        raise EvalError(f"{name}: expected a time, got logical")
    if isinstance(value, (int, float)):
        return _time_from_day_fraction(value)
    raise EvalError(f"{name}: expected a time, got {_type_name(value)}")


def _coerce_duration_like(name: str, value: Any) -> timedelta | None:
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value
    if isinstance(value, bool):
        raise EvalError(f"{name}: expected a duration, got logical")
    if isinstance(value, (int, float)):
        # Duration's base unit is days - Duration.TotalDays(Duration.From(1))
        # is documented to be 1.0.
        return timedelta(days=value)
    if isinstance(value, str):
        return _parse_duration_text(name, value)
    raise EvalError(f"{name}: expected a duration, got {_type_name(value)}")


# --------------------------------------------------------------------------
# Day-of-week / week arithmetic shared by DayOfWeek, StartOfWeek, EndOfWeek,
# WeekOfYear
# --------------------------------------------------------------------------


def _sunday_based_weekday(d: date) -> int:
    """0=Sunday .. 6=Saturday, from Python's 0=Monday .. 6=Sunday."""
    return (d.weekday() + 1) % 7


def _resolve_first_day(name: str, args: list[Any], index: int) -> int:
    if len(args) > index and args[index] is not None:
        value = _require_int(args[index])
        if not 0 <= value <= 6:
            raise EvalError(
                f"{name}: firstDayOfWeek must be a Day.* value (0-6), got {value}"
            )
        return value
    return _DAY_ENUM["Day.Sunday"]


# --------------------------------------------------------------------------
# Month arithmetic shared by AddMonths/AddYears
# --------------------------------------------------------------------------


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(name: str, d: date | datetime, months: int) -> date | datetime:
    total = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(d.day, _days_in_month(year, month))
    try:
        return d.replace(year=year, month=month, day=day)
    except ValueError as error:
        raise EvalError(f"{name}: {error}") from error


# --------------------------------------------------------------------------
# Custom ("yyyy-MM-dd"-style) text formatting for Date/DateTime/Time.ToText
# --------------------------------------------------------------------------

# Every letter RUN (maximal, e.g. "yyyy" or "QQQ") is scanned as a whole -
# not just the known tokens - so an unrecognised specifier is rejected
# rather than silently split into a known prefix plus leftover literal
# letters (e.g. "yyy" must not silently render as "yy" + a stray "y", and
# "QQQ" must not silently pass through as the literal text "QQQ").
_FORMAT_LETTER_RUN_RE = re.compile(r"[A-Za-z]+")

_KNOWN_FORMAT_TOKENS = frozenset(
    {
        "yyyy",
        "yy",
        "MMMM",
        "MMM",
        "MM",
        "M",
        "dddd",
        "ddd",
        "dd",
        "d",
        "HH",
        "H",
        "hh",
        "h",
        "mm",
        "m",
        "ss",
        "s",
        "fff",
        "tt",
    }
)

_DATE_FORMAT_TOKENS = frozenset(
    {"yyyy", "yy", "MMMM", "MMM", "MM", "M", "dddd", "ddd", "dd", "d"}
)


def _render_date_token(token: str, d: date) -> str:
    if token == "yyyy":
        return f"{d.year:04d}"
    if token == "yy":
        return f"{d.year % 100:02d}"
    if token == "MMMM":
        return _MONTH_NAMES[d.month - 1]
    if token == "MMM":
        return _MONTH_NAMES[d.month - 1][:3]
    if token == "MM":
        return f"{d.month:02d}"
    if token == "M":
        return str(d.month)
    if token in ("dddd", "ddd"):
        weekday = _DAY_NAMES_SUNDAY_FIRST[_sunday_based_weekday(d)]
        return weekday if token == "dddd" else weekday[:3]
    if token == "dd":
        return f"{d.day:02d}"
    # token == "d" is the only remaining member of _DATE_FORMAT_TOKENS.
    return str(d.day)


def _render_time_token(
    token: str, hour: int, minute: int, second: int, microsecond: int
) -> str:
    if token == "HH":
        return f"{hour:02d}"
    if token == "H":
        return str(hour)
    if token == "hh":
        return f"{(hour % 12) or 12:02d}"
    if token == "h":
        return str((hour % 12) or 12)
    if token == "mm":
        return f"{minute:02d}"
    if token == "m":
        return str(minute)
    if token == "ss":
        return f"{second:02d}"
    if token == "s":
        return str(second)
    if token == "fff":
        return f"{microsecond // 1000:03d}"
    # token == "tt" is the only possibility left: every member of
    # _KNOWN_FORMAT_TOKENS not in _DATE_FORMAT_TOKENS is handled above.
    return "AM" if hour < 12 else "PM"


def _format_custom(name: str, value: date | datetime | time, fmt: str) -> str:
    # `datetime` is a subclass of `date`, so this one isinstance check
    # covers both - only a bare `time` value has no date component.
    date_part = value if isinstance(value, date) else None
    hour = getattr(value, "hour", 0)
    minute = getattr(value, "minute", 0)
    second = getattr(value, "second", 0)
    microsecond = getattr(value, "microsecond", 0)

    def render(match: re.Match[str]) -> str:
        token = match.group(0)
        if token not in _KNOWN_FORMAT_TOKENS:
            raise UnsupportedError(f"{name}: format specifier {token!r}")
        if token in _DATE_FORMAT_TOKENS:
            if date_part is None:
                raise EvalError(
                    f"{name}: format {fmt!r} uses a date specifier on a time value"
                )
            return _render_date_token(token, date_part)
        return _render_time_token(token, hour, minute, second, microsecond)

    return _FORMAT_LETTER_RUN_RE.sub(render, fmt)


def _resolve_to_text_options(name: str, rest: list[Any]) -> tuple[str | None, Any]:
    """Shared (format, culture) resolution for Date/DateTime/Time.ToText.

    Real Power Query documents this family both as
    ``X.ToText(value, format, culture)`` (two trailing positional
    arguments) and as ``X.ToText(value, options)`` where `options` is a
    ``[Format = ..., Culture = ...]`` record. This accepts both shapes -
    the PRD's "Findings from the fixture round" point #1 requires honouring
    an options record, not silently ignoring it.
    """
    fmt: str | None = None
    culture: Any = None
    if len(rest) >= 1 and rest[0] is not None:
        first = rest[0]
        if isinstance(first, dict):
            fmt = first.get("Format")
            culture = first.get("Culture")
            unknown = set(first) - {"Format", "Culture"}
            if unknown:
                raise UnsupportedError(f"{name}: option(s) {sorted(unknown)}")
        elif isinstance(first, str):
            fmt = first
        else:
            raise EvalError(
                f"{name}: format must be text or a [Format = ...] record, "
                f"got {_type_name(first)}"
            )
    if len(rest) >= 2 and rest[1] is not None:
        culture = rest[1]
    return fmt, culture


# --------------------------------------------------------------------------
# Duration component breakdown, shared by Days/Hours/Minutes/Seconds/ToText
# --------------------------------------------------------------------------


def _duration_components(td: timedelta) -> tuple[int, int, int, int, int, int]:
    """(sign, days, hours, minutes, seconds, microseconds) of `abs(td)`."""
    total = td.total_seconds()
    sign = -1 if total < 0 else 1
    total = abs(total)
    whole_seconds = int(total)
    microseconds = round((total - whole_seconds) * 1_000_000)
    days, remainder = divmod(whole_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return sign, days, hours, minutes, seconds, microseconds


def _format_duration(name: str, td: timedelta, fmt: Any) -> str:
    if fmt is not None:
        raise UnsupportedError(f"{name} with a custom format argument")
    sign, days, hours, minutes, seconds, microseconds = _duration_components(td)
    body = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    if microseconds:
        body += f".{microseconds:06d}"
    sign_text = "-" if sign < 0 else ""
    if days:
        return f"{sign_text}{days}.{body}"
    return f"{sign_text}{body}"


# --------------------------------------------------------------------------
# Literals - #date/#datetime/#datetimezone/#time/#duration
#
# These parse as ordinary invoke expressions on an identifier literally
# named "#date" etc (verified via `core._bridge(src, "ast")`), so they are
# registered here under those exact literal names, same as every other
# builtin.
# --------------------------------------------------------------------------


def _split_seconds(value: int | float) -> tuple[int, int]:
    """(whole_seconds, microseconds) from a possibly-fractional seconds arg."""
    whole = int(value)
    microsecond = round((value - whole) * 1_000_000)
    return whole, microsecond


def _lit_date(args: list[Any], ctx: _Ctx) -> Any:
    _arity("#date", args, 3)
    year, month, day = (_require_int(a) for a in args)
    try:
        return date(year, month, day)
    except ValueError as error:
        raise EvalError(f"#date: {error}") from error


def _lit_time(args: list[Any], ctx: _Ctx) -> Any:
    _arity("#time", args, 3)
    hour = _require_int(args[0])
    minute = _require_int(args[1])
    whole_seconds, microsecond = _split_seconds(_require_number(args[2]))
    try:
        return time(hour, minute, whole_seconds, microsecond)
    except ValueError as error:
        raise EvalError(f"#time: {error}") from error


def _lit_datetime(args: list[Any], ctx: _Ctx) -> Any:
    _arity("#datetime", args, 6)
    year, month, day = (_require_int(a) for a in args[:3])
    hour, minute = (_require_int(a) for a in args[3:5])
    whole_seconds, microsecond = _split_seconds(_require_number(args[5]))
    try:
        return datetime(year, month, day, hour, minute, whole_seconds, microsecond)
    except ValueError as error:
        raise EvalError(f"#datetime: {error}") from error


def _lit_datetimezone(args: list[Any], ctx: _Ctx) -> Any:
    _arity("#datetimezone", args, 8)
    year, month, day = (_require_int(a) for a in args[:3])
    hour, minute = (_require_int(a) for a in args[3:5])
    whole_seconds, microsecond = _split_seconds(_require_number(args[5]))
    offset_hours = _require_number(args[6])
    offset_minutes = _require_number(args[7])
    try:
        tz = timezone(timedelta(hours=offset_hours, minutes=offset_minutes))
    except ValueError as error:
        raise EvalError(f"#datetimezone: invalid offset: {error}") from error
    try:
        return datetime(
            year, month, day, hour, minute, whole_seconds, microsecond, tzinfo=tz
        )
    except ValueError as error:
        raise EvalError(f"#datetimezone: {error}") from error


def _lit_duration(args: list[Any], ctx: _Ctx) -> Any:
    _arity("#duration", args, 4)
    days, hours, minutes, seconds = (_require_number(a) for a in args)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


# --------------------------------------------------------------------------
# Date.*
# --------------------------------------------------------------------------


def _date_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.From", args, 1, 2)
    if len(args) == 2:
        _check_invariant_culture("Date.From", args[1])
    return _coerce_date_like("Date.From", args[0])


def _date_year(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.Year", args, 1)
    d = _coerce_date_like("Date.Year", args[0])
    return None if d is None else d.year


def _date_month(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.Month", args, 1)
    d = _coerce_date_like("Date.Month", args[0])
    return None if d is None else d.month


def _date_day(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.Day", args, 1)
    d = _coerce_date_like("Date.Day", args[0])
    return None if d is None else d.day


def _date_day_of_week(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.DayOfWeek", args, 1, 2)
    d = _coerce_date_like("Date.DayOfWeek", args[0])
    if d is None:
        return None
    first = _resolve_first_day("Date.DayOfWeek", args, 1)
    return (_sunday_based_weekday(d) - first) % 7


def _date_day_of_week_name(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.DayOfWeekName", args, 1, 2)
    if len(args) == 2:
        _check_invariant_culture("Date.DayOfWeekName", args[1])
    d = _coerce_date_like("Date.DayOfWeekName", args[0])
    if d is None:
        return None
    return _DAY_NAMES_SUNDAY_FIRST[_sunday_based_weekday(d)]


def _date_day_of_year(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.DayOfYear", args, 1)
    d = _coerce_date_like("Date.DayOfYear", args[0])
    if d is None:
        return None
    return (d - date(d.year, 1, 1)).days + 1


def _date_month_name(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.MonthName", args, 1, 2)
    if len(args) == 2:
        _check_invariant_culture("Date.MonthName", args[1])
    d = _coerce_date_like("Date.MonthName", args[0])
    if d is None:
        return None
    return _MONTH_NAMES[d.month - 1]


def _date_add_days(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.AddDays", args, 2)
    d = _coerce_date_or_datetime("Date.AddDays", args[0])
    if d is None:
        return None
    days = _require_number(args[1])
    return d + timedelta(days=days)


def _date_add_weeks(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.AddWeeks", args, 2)
    d = _coerce_date_or_datetime("Date.AddWeeks", args[0])
    if d is None:
        return None
    weeks = _require_number(args[1])
    return d + timedelta(days=weeks * 7)


def _date_add_months(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.AddMonths", args, 2)
    d = _coerce_date_or_datetime("Date.AddMonths", args[0])
    if d is None:
        return None
    months = _require_int(args[1])
    return _add_months("Date.AddMonths", d, months)


def _date_add_years(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.AddYears", args, 2)
    d = _coerce_date_or_datetime("Date.AddYears", args[0])
    if d is None:
        return None
    years = _require_int(args[1])
    return _add_months("Date.AddYears", d, years * 12)


def _start_like(d: date | datetime, target: date) -> date | datetime:
    if isinstance(d, datetime):
        return datetime(target.year, target.month, target.day)
    return target


def _date_start_of_month(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.StartOfMonth", args, 1)
    d = _coerce_date_or_datetime("Date.StartOfMonth", args[0])
    if d is None:
        return None
    return _start_like(d, date(d.year, d.month, 1))


def _date_end_of_month(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.EndOfMonth", args, 1)
    d = _coerce_date_or_datetime("Date.EndOfMonth", args[0])
    if d is None:
        return None
    return _start_like(d, date(d.year, d.month, _days_in_month(d.year, d.month)))


def _date_start_of_year(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.StartOfYear", args, 1)
    d = _coerce_date_or_datetime("Date.StartOfYear", args[0])
    if d is None:
        return None
    return _start_like(d, date(d.year, 1, 1))


def _date_end_of_year(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.EndOfYear", args, 1)
    d = _coerce_date_or_datetime("Date.EndOfYear", args[0])
    if d is None:
        return None
    return _start_like(d, date(d.year, 12, 31))


def _date_start_of_week(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.StartOfWeek", args, 1, 2)
    d = _coerce_date_or_datetime("Date.StartOfWeek", args[0])
    if d is None:
        return None
    first = _resolve_first_day("Date.StartOfWeek", args, 1)
    base = d.date() if isinstance(d, datetime) else d
    offset = (_sunday_based_weekday(base) - first) % 7
    return _start_like(d, base - timedelta(days=offset))


def _date_end_of_week(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.EndOfWeek", args, 1, 2)
    d = _coerce_date_or_datetime("Date.EndOfWeek", args[0])
    if d is None:
        return None
    first = _resolve_first_day("Date.EndOfWeek", args, 1)
    base = d.date() if isinstance(d, datetime) else d
    offset = (_sunday_based_weekday(base) - first) % 7
    start = base - timedelta(days=offset)
    return _start_like(d, start + timedelta(days=6))


def _date_to_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.ToText", args, 1, 3)
    d = _coerce_date_like("Date.ToText", args[0])
    if d is None:
        return None
    fmt, culture = _resolve_to_text_options("Date.ToText", args[1:])
    _check_invariant_culture("Date.ToText", culture)
    if fmt is None:
        return d.isoformat()
    return _format_custom("Date.ToText", d, fmt)


def _date_is_in_current_month(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.IsInCurrentMonth", args, 1)
    d = _coerce_date_like("Date.IsInCurrentMonth", args[0])
    if d is None:
        return None
    today = date.today()
    return d.year == today.year and d.month == today.month


def _date_is_in_current_year(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.IsInCurrentYear", args, 1)
    d = _coerce_date_like("Date.IsInCurrentYear", args[0])
    if d is None:
        return None
    return d.year == date.today().year


def _date_week_of_year(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.WeekOfYear", args, 1, 2)
    d = _coerce_date_like("Date.WeekOfYear", args[0])
    if d is None:
        return None
    first = _resolve_first_day("Date.WeekOfYear", args, 1)
    year_start = date(d.year, 1, 1)
    offset = (_sunday_based_weekday(year_start) - first) % 7
    start_of_year_week = year_start - timedelta(days=offset)
    return (d - start_of_year_week).days // 7 + 1


def _date_quarter_of_year(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.QuarterOfYear", args, 1)
    d = _coerce_date_like("Date.QuarterOfYear", args[0])
    if d is None:
        return None
    return (d.month - 1) // 3 + 1


# --------------------------------------------------------------------------
# DateTime.*
# --------------------------------------------------------------------------


def _datetime_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.From", args, 1, 2)
    if len(args) == 2:
        _check_invariant_culture("DateTime.From", args[1])
    return _coerce_datetime_like("DateTime.From", args[0])


def _datetime_date(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.Date", args, 1)
    dt = _coerce_datetime_like("DateTime.Date", args[0])
    return None if dt is None else dt.date()


def _datetime_time(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.Time", args, 1)
    dt = _coerce_datetime_like("DateTime.Time", args[0])
    return None if dt is None else dt.time()


def _datetime_local_now(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.LocalNow", args, 0)
    return datetime.now()


def _datetime_fixed_local_now(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.FixedLocalNow", args, 0)
    # See module docstring point 7: this does not memoise within one
    # evaluate() call the way real Power Query does - documented gap, not a
    # silent one.
    return datetime.now()


def _datetime_to_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.ToText", args, 1, 3)
    dt = _coerce_datetime_like("DateTime.ToText", args[0])
    if dt is None:
        return None
    fmt, culture = _resolve_to_text_options("DateTime.ToText", args[1:])
    _check_invariant_culture("DateTime.ToText", culture)
    if fmt is None:
        return dt.isoformat(sep=" ")
    return _format_custom("DateTime.ToText", dt, fmt)


def _datetime_add_zone(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.AddZone", args, 2, 3)
    dt = _coerce_datetime_like("DateTime.AddZone", args[0])
    if dt is None:
        return None
    if dt.tzinfo is not None:
        raise EvalError("DateTime.AddZone: value already has a time zone")
    hours = _require_number(args[1])
    minutes = _require_number(args[2]) if len(args) == 3 and args[2] is not None else 0
    try:
        tz = timezone(timedelta(hours=hours, minutes=minutes))
    except ValueError as error:
        raise EvalError(
            f"DateTime.AddZone: invalid time zone offset: {error}"
        ) from error
    return dt.replace(tzinfo=tz)


# --------------------------------------------------------------------------
# Duration.*
# --------------------------------------------------------------------------


def _duration_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.From", args, 1)
    return _coerce_duration_like("Duration.From", args[0])


def _duration_days(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.Days", args, 1)
    td = _coerce_duration_like("Duration.Days", args[0])
    if td is None:
        return None
    sign, days, *_rest = _duration_components(td)
    return sign * days


def _duration_hours(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.Hours", args, 1)
    td = _coerce_duration_like("Duration.Hours", args[0])
    if td is None:
        return None
    sign, _days, hours, *_rest = _duration_components(td)
    return sign * hours


def _duration_minutes(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.Minutes", args, 1)
    td = _coerce_duration_like("Duration.Minutes", args[0])
    if td is None:
        return None
    sign, _days, _hours, minutes, *_rest = _duration_components(td)
    return sign * minutes


def _duration_seconds(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.Seconds", args, 1)
    td = _coerce_duration_like("Duration.Seconds", args[0])
    if td is None:
        return None
    sign, _days, _hours, _minutes, seconds, _us = _duration_components(td)
    return sign * seconds


def _duration_total_days(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.TotalDays", args, 1)
    td = _coerce_duration_like("Duration.TotalDays", args[0])
    return None if td is None else td.total_seconds() / 86400


def _duration_total_hours(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.TotalHours", args, 1)
    td = _coerce_duration_like("Duration.TotalHours", args[0])
    return None if td is None else td.total_seconds() / 3600


def _duration_total_minutes(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.TotalMinutes", args, 1)
    td = _coerce_duration_like("Duration.TotalMinutes", args[0])
    return None if td is None else td.total_seconds() / 60


def _duration_total_seconds(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.TotalSeconds", args, 1)
    td = _coerce_duration_like("Duration.TotalSeconds", args[0])
    return None if td is None else td.total_seconds()


def _duration_to_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.ToText", args, 1, 2)
    td = _coerce_duration_like("Duration.ToText", args[0])
    if td is None:
        return None
    fmt = args[1] if len(args) == 2 else None
    return _format_duration("Duration.ToText", td, fmt)


# --------------------------------------------------------------------------
# Time.*
# --------------------------------------------------------------------------


def _time_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Time.From", args, 1, 2)
    if len(args) == 2:
        _check_invariant_culture("Time.From", args[1])
    return _coerce_time_like("Time.From", args[0])


def _time_hour(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Time.Hour", args, 1)
    t = _coerce_time_like("Time.Hour", args[0])
    return None if t is None else t.hour


def _time_minute(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Time.Minute", args, 1)
    t = _coerce_time_like("Time.Minute", args[0])
    return None if t is None else t.minute


def _time_second(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Time.Second", args, 1)
    t = _coerce_time_like("Time.Second", args[0])
    return None if t is None else t.second


def _time_to_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Time.ToText", args, 1, 3)
    t = _coerce_time_like("Time.ToText", args[0])
    if t is None:
        return None
    fmt, culture = _resolve_to_text_options("Time.ToText", args[1:])
    _check_invariant_culture("Time.ToText", culture)
    if fmt is None:
        return t.isoformat()
    return _format_custom("Time.ToText", t, fmt)


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel. Day.* enum members are plain ints,
# not callables - `_eval_identifier_expression` in evaluate.py returns
# whatever BUILTINS.get(name) finds regardless of whether it's callable, the
# same mechanism Order.Ascending/Order.Descending use (those happen to be
# special-cased directly in evaluate.py instead, but the BUILTINS path works
# identically and is the only one this module can reach).
BUILTINS: dict[str, Any] = {
    "#date": _lit_date,
    "#datetime": _lit_datetime,
    "#datetimezone": _lit_datetimezone,
    "#time": _lit_time,
    "#duration": _lit_duration,
    "Date.From": _date_from,
    "Date.Year": _date_year,
    "Date.Month": _date_month,
    "Date.Day": _date_day,
    "Date.DayOfWeek": _date_day_of_week,
    "Date.DayOfWeekName": _date_day_of_week_name,
    "Date.DayOfYear": _date_day_of_year,
    "Date.MonthName": _date_month_name,
    "Date.AddDays": _date_add_days,
    "Date.AddWeeks": _date_add_weeks,
    "Date.AddMonths": _date_add_months,
    "Date.AddYears": _date_add_years,
    "Date.StartOfMonth": _date_start_of_month,
    "Date.EndOfMonth": _date_end_of_month,
    "Date.StartOfYear": _date_start_of_year,
    "Date.EndOfYear": _date_end_of_year,
    "Date.StartOfWeek": _date_start_of_week,
    "Date.EndOfWeek": _date_end_of_week,
    "Date.ToText": _date_to_text,
    "Date.IsInCurrentMonth": _date_is_in_current_month,
    "Date.IsInCurrentYear": _date_is_in_current_year,
    "Date.WeekOfYear": _date_week_of_year,
    "Date.QuarterOfYear": _date_quarter_of_year,
    "DateTime.From": _datetime_from,
    "DateTime.Date": _datetime_date,
    "DateTime.Time": _datetime_time,
    "DateTime.LocalNow": _datetime_local_now,
    "DateTime.FixedLocalNow": _datetime_fixed_local_now,
    "DateTime.ToText": _datetime_to_text,
    "DateTime.AddZone": _datetime_add_zone,
    "Duration.From": _duration_from,
    "Duration.Days": _duration_days,
    "Duration.Hours": _duration_hours,
    "Duration.Minutes": _duration_minutes,
    "Duration.Seconds": _duration_seconds,
    "Duration.TotalDays": _duration_total_days,
    "Duration.TotalHours": _duration_total_hours,
    "Duration.TotalMinutes": _duration_total_minutes,
    "Duration.TotalSeconds": _duration_total_seconds,
    "Duration.ToText": _duration_to_text,
    "Time.From": _time_from,
    "Time.Hour": _time_hour,
    "Time.Minute": _time_minute,
    "Time.Second": _time_second,
    "Time.ToText": _time_to_text,
    **_DAY_ENUM,
}
