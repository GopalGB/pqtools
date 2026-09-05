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
   to ``EvalError(f"unsupported value type: {_type_name(value)}")``, which
   now names the M type - ``date``/``datetime``/``datetimezone``/``time``/
   ``duration``. That is correct Power Query behaviour: real ``Text.From``
   does not accept date-family values either - callers are expected to
   reach for ``Date.ToText``/``DateTime.ToText``/``Time.ToText``/
   ``Duration.ToText``, which this module provides.
3. **The M comparison and arithmetic operators DO work on these values as
   of 0.10.0.** This note recorded the opposite for three releases, and the
   gap was real: ``#date(2024,1,1) = #date(2024,1,1)`` was silently
   ``False``, and ``#date(2024,1,2) > #date(2024,1,1)`` raised. Both are
   fixed in the files this module cannot reach - ``_shared._m_equal`` grew
   a temporal branch, and ``evaluate``'s operator layer was rewritten
   against the M specification's own operand tables. ``#date + #duration``,
   ``#datetime - #datetime``, the four relational operators and
   ``=``/``<>`` now behave here as they do in Power Query, so the values
   this module produces can be compared and offset directly. See
   ``tests/test_operators.py`` and ``tests/test_temporal_comparison.py``.
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
   task brief's trap #3. The same applies to
   ``DateTimeZone.LocalNow``/``FixedLocalNow``/``UtcNow``/``FixedUtcNow``.
8. **A handful of functions are HOST-dependent by specification, not by
   shortcut.** ``DateTimeZone.From`` (for a date/datetime/time/number
   argument), ``DateTimeZone.ToLocal``, ``DateTime.FromFileTime`` and
   ``DateTimeZone.FromFileTime`` are all documented to resolve "the local
   time zone" - Microsoft's own ``DateTimeZone.From`` page says the answer
   "is different when running this function locally as opposed to running
   it online". These use Python's ``datetime.astimezone()``, which asks the
   HOST's real timezone database (via the OS) for the correct offset at
   that exact instant, DST included - a real lookup, never a guessed
   offset, so it is not the fabricated-timezone-data case this module
   refuses. It is still machine-dependent output, so no test asserts an
   exact wall-clock value for them; they are cross-checked against an
   independent same-host recomputation instead.
9. **End-of-period values land on 23:59:59.999999, and keep their zone.**
   ``Date.EndOfDay``/``EndOfWeek``/``EndOfMonth``/``EndOfQuarter``/
   ``EndOfYear`` and ``Time.EndOfHour`` return the last instant of the
   period for a datetime/datetimezone argument (a plain ``date`` argument
   still returns a plain ``date``). Real Power Query shows
   ``23:59:59.9999999`` - one 100-nanosecond tick before the boundary -
   but ``datetime`` only carries microseconds, so 999999 is the closest
   value this representation holds; see ``_end_like``. This CHANGED
   ``Date.EndOfMonth``/``EndOfYear``/``EndOfWeek``, which previously
   returned MIDNIGHT of the correct day (contradicting the documented
   examples) and silently dropped ``tzinfo`` from a datetimezone argument;
   ``Date.StartOf*`` kept its midnight semantics but stopped dropping
   ``tzinfo``.
10. **The DateTimeZone.* family is strictly typed, deliberately.** Every
   ``DateTimeZone.*`` function except ``From``/``FromText``/``FromFileTime``
   declares its parameter as ``datetimezone`` (not ``any``) in the docs,
   unlike the whole ``Date.*``/``DateTime.*`` accessor family - so those
   take an already-aware value only, and a naive ``datetime`` is a type
   error rather than a value with a guessed zone. ``ToLocal``/``ToUtc`` are
   the two exceptions whose own docs say what a zone-less value does.
"""

from __future__ import annotations

import calendar
import math
import re
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _require_int,
    _require_number,
    _type_name,
)
from ._shared import (
    _check_invariant_culture as _shared_check_culture,
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


# --------------------------------------------------------------------------
# Culture handling (trap #2) - invariant/English only, else UnsupportedError
# --------------------------------------------------------------------------


def _check_invariant_culture(name: str, culture: Any) -> None:
    _shared_check_culture(name, culture, "culture-specific formatting")


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


def _quarter_bounds(year: int, month: int) -> tuple[date, date]:
    """(first day, last day) of the calendar quarter containing `month`."""
    start_month = (month - 1) // 3 * 3 + 1
    end_month = start_month + 2
    return date(year, start_month, 1), date(
        year, end_month, _days_in_month(year, end_month)
    )


def _quarter_index(d: date) -> int:
    """A single monotonic integer per quarter, for offset arithmetic."""
    return d.year * 4 + (d.month - 1) // 3


# --------------------------------------------------------------------------
# Custom ("yyyy-MM-dd"-style) text formatting for Date/DateTime/Time.ToText
# --------------------------------------------------------------------------

# A token is a maximal run of the SAME repeated letter (e.g. "yyyy" or
# "QQQ") - matching real .NET's own custom-format tokenizer, which is why
# "yyyyMMdd" with no separators parses as three tokens ("yyyy", "MM", "dd")
# rather than one 8-letter blob. An unrecognised run (same-letter or not)
# is still rejected as a whole, never silently split into a known prefix
# plus leftover literal letters ("yyy" must not render as "yy" + stray "y",
# "QQQ" must not pass through as literal text "QQQ").
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
        "fffffff",
        "tt",
    }
)

_DATE_FORMAT_TOKENS = frozenset(
    {"yyyy", "yy", "MMMM", "MMM", "MM", "M", "dddd", "ddd", "dd", "d"}
)

# "zzz" (signed UTC offset, "+05:30") is neither a date nor a plain
# hour/minute/second token - it needs the value's tzinfo, not its wall-clock
# fields, so it is dispatched separately in `_format_custom` below.
_ZONE_FORMAT_TOKENS = frozenset({"zzz"})


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
    if token == "fffffff":
        # 100-nanosecond "ticks" - a `datetime.time` only carries
        # microsecond precision, so the 7th digit is always 0, not a guess.
        return f"{microsecond * 10:07d}"
    # token == "tt" is the only possibility left: every member of
    # _KNOWN_FORMAT_TOKENS not in _DATE_FORMAT_TOKENS/_ZONE_FORMAT_TOKENS is
    # handled above or by _render_zone_token.
    return "AM" if hour < 12 else "PM"


def _render_zone_token(name: str, fmt: str, offset: timedelta | None) -> str:
    if offset is None:
        raise EvalError(
            f"{name}: format {fmt!r} uses a time-zone specifier on a value "
            "with no time zone"
        )
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _format_custom(name: str, value: date | datetime | time, fmt: str) -> str:
    # `datetime` is a subclass of `date`, so this one isinstance check
    # covers both - only a bare `time` value has no date component.
    date_part = value if isinstance(value, date) else None
    hour = getattr(value, "hour", 0)
    minute = getattr(value, "minute", 0)
    second = getattr(value, "second", 0)
    microsecond = getattr(value, "microsecond", 0)
    # `time` values never carry tzinfo in this module's value model (see
    # _coerce_time_like) - the isinstance check still guards defensively
    # rather than assuming that of every caller. A plain `date` has no
    # `.utcoffset()` at all, hence the explicit type narrowing (not a bare
    # `getattr`, which mypy cannot use to narrow `value`'s type).
    utc_offset: timedelta | None = None
    if isinstance(value, (datetime, time)) and value.tzinfo is not None:
        utc_offset = value.utcoffset()

    out: list[str] = []
    i, n = 0, len(fmt)
    while i < n:
        ch = fmt[i]
        if ch in ("'", '"'):
            # A quoted run is literal text, verbatim, never scanned for
            # tokens - the standard-format expansions below rely on this to
            # spell a literal "T"/"Z"/"GMT" that would otherwise collide
            # with a real token letter.
            end = fmt.find(ch, i + 1)
            if end == -1:
                raise EvalError(f"{name}: unterminated literal in format {fmt!r}")
            out.append(fmt[i + 1 : end])
            i = end + 1
            continue
        if ch == "\\":
            if i + 1 >= n:
                raise EvalError(f"{name}: trailing '\\\\' in format {fmt!r}")
            out.append(fmt[i + 1])
            i += 2
            continue
        if ch.isalpha():
            j = i
            while j < n and fmt[j] == ch:
                j += 1
            token = fmt[i:j]
            if token in _ZONE_FORMAT_TOKENS:
                out.append(_render_zone_token(name, fmt, utc_offset))
            elif token in _KNOWN_FORMAT_TOKENS:
                if token in _DATE_FORMAT_TOKENS:
                    if date_part is None:
                        raise EvalError(
                            f"{name}: format {fmt!r} uses a date specifier "
                            "on a time value"
                        )
                    out.append(_render_date_token(token, date_part))
                else:
                    out.append(
                        _render_time_token(token, hour, minute, second, microsecond)
                    )
            else:
                raise UnsupportedError(f"{name}: format specifier {token!r}")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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
# Standard ("d"/"D"/"F"/... single-letter) formats for Date/DateTime/Time.
# ToText - a DIFFERENT feature from the custom patterns above: real .NET
# treats a format string with exactly one character as a standard format
# (selecting a whole culture-defined pattern), and anything longer as a
# custom pattern scanned token-by-token - verified against "Standard date
# and time format strings" in the .NET docs. Patterns below are this
# module's own en-US data (module docstring point 6), expressed as the
# custom tokens `_format_custom` already renders, so there is exactly one
# rendering engine, not two.
# --------------------------------------------------------------------------

_STANDARD_DATETIME_PATTERNS: dict[str, str] = {
    "d": "M/d/yyyy",
    "D": "dddd, MMMM d, yyyy",
    "f": "dddd, MMMM d, yyyy h:mm tt",
    "F": "dddd, MMMM d, yyyy h:mm:ss tt",
    "g": "M/d/yyyy h:mm tt",
    "G": "M/d/yyyy h:mm:ss tt",
    "m": "MMMM d",
    "M": "MMMM d",
    "t": "h:mm tt",
    "T": "h:mm:ss tt",
    "y": "MMMM yyyy",
    "Y": "MMMM yyyy",
}
_STANDARD_DATE_ONLY_LETTERS = frozenset("dDmMyY")
_STANDARD_TIME_ONLY_LETTERS = frozenset("tT")
# Every standard letter this module knows about, datetime-only ones (f, F,
# g, G, o, O, r, R, s, t is date+time already listed, u, U) included - used
# only to distinguish "wrong shape for this value" from "not a real
# specifier at all" in the error message below.
_ALL_STANDARD_LETTERS = frozenset("dDfFgGmMoOrRstTuUyY")


def _render_round_trip(name: str, value: datetime, fmt: str) -> str:
    body = _format_custom(name, value, "yyyy-MM-dd'T'HH:mm:ss")
    body += f".{value.microsecond * 10:07d}"
    if value.tzinfo is None:
        return body
    offset = value.utcoffset()
    if offset == timedelta(0):
        return body + "Z"
    return body + _render_zone_token(name, fmt, offset)


def _render_rfc1123(name: str, value: datetime) -> str:
    # Real .NET converts an offset-aware value to UTC first (verified: the
    # docs' own DateTimeOffset example shows the hour shifting); a naive
    # value is rendered as-is with a "GMT" label, no conversion.
    rendered = value.astimezone(UTC) if value.tzinfo is not None else value
    return _format_custom(name, rendered, "ddd, dd MMM yyyy HH:mm:ss 'GMT'")


def _render_universal_sortable(name: str, value: datetime) -> str:
    rendered = value.astimezone(UTC) if value.tzinfo is not None else value
    return _format_custom(name, rendered, "yyyy-MM-dd HH:mm:ss'Z'")


def _render_universal_full(name: str, value: datetime, fmt: str) -> str:
    if value.tzinfo is None:
        # Real .NET resolves this case using the MACHINE's local time zone
        # (DateTimeKind.Unspecified is treated as local) - exactly the kind
        # of host-dependent result this module refuses to produce (see the
        # LocalNow/FixedLocalNow gap noted in point 7 above).
        raise UnsupportedError(
            f"{name}: format {fmt!r} on a value with no time zone (real "
            ".NET falls back to the machine's local time zone here, which "
            "pqtools deliberately never depends on)"
        )
    rendered = value.astimezone(UTC)
    return _format_custom(name, rendered, "dddd, MMMM d, yyyy h:mm:ss tt")


def _resolve_standard_format(
    name: str, value: date | datetime | time, letter: str
) -> str:
    if isinstance(value, datetime):
        pattern = _STANDARD_DATETIME_PATTERNS.get(letter)
        if pattern is not None:
            return _format_custom(name, value, pattern)
        if letter == "s":
            return _format_custom(name, value, "yyyy-MM-dd'T'HH:mm:ss")
        if letter in ("o", "O"):
            return _render_round_trip(name, value, letter)
        if letter in ("r", "R"):
            return _render_rfc1123(name, value)
        if letter == "u":
            return _render_universal_sortable(name, value)
        if letter == "U":
            return _render_universal_full(name, value, letter)
        raise UnsupportedError(f"{name}: format {letter!r}")
    if isinstance(value, date):
        if letter in _STANDARD_DATE_ONLY_LETTERS:
            return _format_custom(name, value, _STANDARD_DATETIME_PATTERNS[letter])
        if letter in _ALL_STANDARD_LETTERS:
            raise UnsupportedError(
                f"{name}: format {letter!r} requires a time component, "
                "which a date value does not have"
            )
        raise UnsupportedError(f"{name}: format {letter!r}")
    # The only remaining shape is a bare `time`.
    if letter in _STANDARD_TIME_ONLY_LETTERS:
        return _format_custom(name, value, _STANDARD_DATETIME_PATTERNS[letter])
    if letter in _ALL_STANDARD_LETTERS:
        raise UnsupportedError(
            f"{name}: format {letter!r} requires a date component, which a "
            "time value does not have"
        )
    raise UnsupportedError(f"{name}: format {letter!r}")


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
# Relative-period predicates: the Date.IsIn{Current,Next,Previous}{Day,
# Week,Month,Quarter,Year}[N] and DateTime.IsIn{...}{Hour,Minute,Second}[N]
# families (40 M names total). One engine, parameterised by unit, backs
# every one of them - hand-writing 40 near-identical bodies is exactly the
# kind of duplication that hides the day two of them drift apart.
#
# All ten "Next"/"Previous" docs pages state the rule identically for their
# unit ("...will return false when passed a value that occurs within the
# current [unit]"), and every "NextN"/"PreviousN" page is the same rule
# widened to a count - verified directly against Date.IsInNextDay,
# Date.IsInNextNDays, Date.IsInPreviousDay, Date.IsInPreviousNDays,
# DateTime.IsInNextHour and DateTime.IsInNextNHours (one fetched example
# per family shape); the remaining units/granularities are inferred from
# that identical template, not independently fetched - each is still
# pinned by an explicit test below rather than assumed silently.
#
# Comparisons anchor to "the current date and time on the system" (every
# fetched page's own wording) - this module reads that as: normalise an
# aware (datetimezone) argument to the SYSTEM's local wall clock before
# comparing periods, rather than comparing in the *value's own* offset. No
# fetched example uses an aware argument, so this is a documented CHOICE,
# not a verified fact - pinned by a dedicated test. The two PRE-EXISTING
# functions in this family (Date.IsInCurrentMonth/Year, already shipped,
# outside this task's "missing functions" scope) do NOT do this
# normalisation; left untouched rather than risk changing shipped
# behaviour no test forced a decision on.
# --------------------------------------------------------------------------


def _localize_naive(dt: datetime) -> datetime:
    """An aware value's SYSTEM-local wall clock, as a naive `datetime`."""
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _period_anchor_date(name: str, value: Any) -> date | None:
    coerced = _coerce_date_or_datetime(name, value)
    if coerced is None:
        return None
    if isinstance(coerced, datetime):
        return _localize_naive(coerced).date()
    return coerced


def _period_anchor_datetime(name: str, value: Any) -> datetime | None:
    coerced = _coerce_datetime_like(name, value)
    if coerced is None:
        return None
    return _localize_naive(coerced)


def _date_period_offset(unit: str, value_date: date, now_date: date) -> int:
    """Signed count of whole `unit` periods from `now_date` to `value_date`."""
    if unit == "day":
        return (value_date - now_date).days
    if unit == "week":
        # No firstDayOfWeek parameter is documented for this family (unlike
        # Date.StartOfWeek/EndOfWeek/WeekOfYear) - Day.Sunday matches this
        # module's own default for those sibling functions.
        first = _DAY_ENUM["Day.Sunday"]
        value_start = value_date - timedelta(
            days=(_sunday_based_weekday(value_date) - first) % 7
        )
        now_start = now_date - timedelta(
            days=(_sunday_based_weekday(now_date) - first) % 7
        )
        return (value_start - now_start).days // 7
    if unit == "month":
        return (value_date.year * 12 + value_date.month) - (
            now_date.year * 12 + now_date.month
        )
    if unit == "quarter":
        return _quarter_index(value_date) - _quarter_index(now_date)
    return value_date.year - now_date.year  # unit == "year"


def _time_period_offset(unit: str, value_dt: datetime, now_dt: datetime) -> int:
    """Signed count of whole `unit` periods from `now_dt` to `value_dt`."""
    if unit == "hour":
        v = value_dt.replace(minute=0, second=0, microsecond=0)
        n = now_dt.replace(minute=0, second=0, microsecond=0)
        denom = 3600
    elif unit == "minute":
        v = value_dt.replace(second=0, microsecond=0)
        n = now_dt.replace(second=0, microsecond=0)
        denom = 60
    else:  # unit == "second"
        v = value_dt.replace(microsecond=0)
        n = now_dt.replace(microsecond=0)
        denom = 1
    return round((v - n).total_seconds() / denom)


def _now_local() -> datetime:
    """The single system-clock read behind every relative-period predicate.

    Called by GLOBAL NAME from the closures below (never captured as a
    parameter) so that a test can freeze it with monkeypatch. Without that
    seam the second/minute-granularity predicates would be genuinely
    flaky: a test that builds "now + 1 second" and asserts
    DateTime.IsInNextSecond is racing the evaluator's own clock read, and
    loses whenever the boundary falls between the two reads.
    """
    return datetime.now()


def _period_now(kind: str) -> Any:
    now = _now_local()
    return now.date() if kind == "date" else now


def _make_is_in(
    name: str,
    resolve: Any,
    kind: str,
    unit: str,
    lo: int,
    hi: int,
) -> Any:
    """IsInCurrentX (lo=hi=0), IsInNextX (lo=hi=1), IsInPreviousX (lo=hi=-1)."""

    def run(args: list[Any], ctx: _Ctx) -> Any:
        _arity(name, args, 1)
        anchor = resolve(name, args[0])
        if anchor is None:
            return None
        offset = _offset_for(kind, unit, anchor, _period_now(kind))
        return lo <= offset <= hi

    run.__name__ = f"_{name.replace('.', '_').lower()}"
    return run


def _make_is_in_n(name: str, resolve: Any, kind: str, unit: str, sign: int) -> Any:
    """IsInNextNX (sign=+1): 0 < offset <= n. IsInPreviousNX: -n <= offset < 0."""

    def run(args: list[Any], ctx: _Ctx) -> Any:
        _arity(name, args, 2)
        anchor = resolve(name, args[0])
        n = _require_int(args[1])
        if anchor is None:
            return None
        offset = _offset_for(kind, unit, anchor, _period_now(kind))
        if sign > 0:
            return 0 < offset <= n
        return -n <= offset < 0

    run.__name__ = f"_{name.replace('.', '_').lower()}"
    return run


def _offset_for(kind: str, unit: str, anchor: Any, now: Any) -> int:
    if kind == "date":
        return _date_period_offset(unit, anchor, now)
    return _time_period_offset(unit, anchor, now)


# --------------------------------------------------------------------------
# Date.*
# --------------------------------------------------------------------------


def _date_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.From", args, 1, 2)
    if len(args) == 2:
        _check_invariant_culture("Date.From", args[1])
    return _coerce_date_like("Date.From", args[0])


def _from_text(name: str, delegate: Any) -> Any:
    """Build ``X.FromText`` from the module's own ``X.From``.

    Delegating rather than re-parsing is the point: a second parser for the
    same family is a second set of edge cases, and the day they disagree the
    symptom is a value that converts one way through a column type and
    another way through an explicit call. M draws the text-only line too, so
    ``Number.FromText(1)`` is an error there as well - accepting it would let
    a column that never held text report a successful text conversion.
    """

    def run(args: list[Any], ctx: _Ctx) -> Any:
        _arity(name, args, 1, 2)
        # "parsing", not the module default "formatting": this direction reads
        # text, and a message about formatting sends the reader to the wrong
        # half of the round trip.
        _shared_check_culture(
            name, args[1] if len(args) == 2 else None, "culture-specific parsing"
        )
        value = args[0]
        if value is None:
            return None
        if not isinstance(value, str):
            raise EvalError(f"{name}: expected text, got {_type_name(value)}")
        try:
            return delegate([value], ctx)
        except EvalError as error:
            # The delegate reports itself, so a failed Number.FromText would
            # otherwise say "Number.From: not a number" and send the reader
            # looking for a call that is not in their query.
            message = str(error)
            prefix = f"{name.split('Text')[0]}: "
            if message.startswith(prefix):
                message = f"{name}: {message[len(prefix) :]}"
            raise EvalError(message) from error

    run.__name__ = f"_{name.replace('.', '_').lower()}"
    return run


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
    """`target` at midnight, in the shape (and time zone) `d` came in as.

    A `date` in produces the target `date` unchanged; a `datetime`/aware
    `datetimezone` in produces midnight of `target` in the SAME tzinfo -
    `datetimezone` is just a `datetime` with `tzinfo` set (module docstring
    point 4), so preserving `d.tzinfo` here is what keeps a datetimezone a
    datetimezone through Start-of-period rather than silently downgrading
    it to a naive datetime.
    """
    if isinstance(d, datetime):
        return datetime(target.year, target.month, target.day, tzinfo=d.tzinfo)
    return target


def _end_like(d: date | datetime, target: date) -> date | datetime:
    """The end-of-period counterpart to `_start_like`.

    A `date` in produces `target` unchanged (a date has no time-of-day to
    push to 23:59:59). A `datetime`/`datetimezone` in produces `target` at
    the last representable instant this module's value model can hold:
    23:59:59.999999. Real Power Query's own docs show this as
    23:59:59.9999999 (2011,5,14 example) - one 100-nanosecond TICK before
    midnight - but `datetime.time` only carries microsecond precision
    (module docstring point 5 makes the same call for the OLE-serial
    fractional part, and `_render_time_token`'s "fffffff" branch already
    documents the identical 6-vs-7-digit ceiling). 999999 microseconds is
    the closest value this representation can hold to that instant, not a
    rounding guess at a different one - the 7th digit is structurally
    unavailable, never fabricated.
    """
    if isinstance(d, datetime):
        return datetime(
            target.year, target.month, target.day, 23, 59, 59, 999_999, tzinfo=d.tzinfo
        )
    return target


def _period_base_date(d: date | datetime) -> date:
    return d.date() if isinstance(d, datetime) else d


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
    return _end_like(d, date(d.year, d.month, _days_in_month(d.year, d.month)))


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
    return _end_like(d, date(d.year, 12, 31))


def _date_start_of_week(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.StartOfWeek", args, 1, 2)
    d = _coerce_date_or_datetime("Date.StartOfWeek", args[0])
    if d is None:
        return None
    first = _resolve_first_day("Date.StartOfWeek", args, 1)
    base = _period_base_date(d)
    offset = (_sunday_based_weekday(base) - first) % 7
    return _start_like(d, base - timedelta(days=offset))


def _date_end_of_week(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.EndOfWeek", args, 1, 2)
    d = _coerce_date_or_datetime("Date.EndOfWeek", args[0])
    if d is None:
        return None
    first = _resolve_first_day("Date.EndOfWeek", args, 1)
    base = _period_base_date(d)
    offset = (_sunday_based_weekday(base) - first) % 7
    start = base - timedelta(days=offset)
    return _end_like(d, start + timedelta(days=6))


def _date_start_of_day(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.StartOfDay", args, 1)
    d = _coerce_date_or_datetime("Date.StartOfDay", args[0])
    if d is None:
        return None
    return _start_like(d, _period_base_date(d))


def _date_end_of_day(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.EndOfDay", args, 1)
    d = _coerce_date_or_datetime("Date.EndOfDay", args[0])
    if d is None:
        return None
    return _end_like(d, _period_base_date(d))


def _date_start_of_quarter(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.StartOfQuarter", args, 1)
    d = _coerce_date_or_datetime("Date.StartOfQuarter", args[0])
    if d is None:
        return None
    base = _period_base_date(d)
    start, _end = _quarter_bounds(base.year, base.month)
    return _start_like(d, start)


def _date_end_of_quarter(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.EndOfQuarter", args, 1)
    d = _coerce_date_or_datetime("Date.EndOfQuarter", args[0])
    if d is None:
        return None
    base = _period_base_date(d)
    _start, end = _quarter_bounds(base.year, base.month)
    return _end_like(d, end)


def _date_add_quarters(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.AddQuarters", args, 2)
    d = _coerce_date_or_datetime("Date.AddQuarters", args[0])
    if d is None:
        return None
    quarters = _require_int(args[1])
    return _add_months("Date.AddQuarters", d, quarters * 3)


def _date_days_in_month(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.DaysInMonth", args, 1)
    d = _coerce_date_like("Date.DaysInMonth", args[0])
    if d is None:
        return None
    return _days_in_month(d.year, d.month)


def _date_is_leap_year(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.IsLeapYear", args, 1)
    d = _coerce_date_like("Date.IsLeapYear", args[0])
    if d is None:
        return None
    return calendar.isleap(d.year)


def _date_to_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.ToRecord", args, 1)
    # Docs declare this `date as date` (strict), unlike every sibling
    # Date.* accessor on this page (`dateTime as any`). Real behaviour for
    # a datetime/text/number argument here is not determined by any
    # fetched example - `_coerce_date_like` is reused for consistency with
    # every other Date.* accessor in this module rather than inventing a
    # second, untested, stricter coercion path. Documented CHOICE, not a
    # verified fact - pinned by test_date_to_record_accepts_datetime_like.
    d = _coerce_date_like("Date.ToRecord", args[0])
    if d is None:
        return None
    return {"Year": d.year, "Month": d.month, "Day": d.day}


def _date_week_of_month(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.WeekOfMonth", args, 1, 2)
    d = _coerce_date_like("Date.WeekOfMonth", args[0])
    if d is None:
        return None
    first = _resolve_first_day("Date.WeekOfMonth", args, 1)
    month_start = date(d.year, d.month, 1)
    offset = (_sunday_based_weekday(month_start) - first) % 7
    start_of_month_week = month_start - timedelta(days=offset)
    return (d - start_of_month_week).days // 7 + 1


def _date_to_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.ToText", args, 1, 3)
    d = _coerce_date_like("Date.ToText", args[0])
    if d is None:
        return None
    fmt, culture = _resolve_to_text_options("Date.ToText", args[1:])
    _check_invariant_culture("Date.ToText", culture)
    if fmt is None:
        return d.isoformat()
    if len(fmt) == 1:
        return _resolve_standard_format("Date.ToText", d, fmt)
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


def _date_is_in_year_to_date(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Date.IsInYearToDate", args, 1)
    anchor = _period_anchor_date("Date.IsInYearToDate", args[0])
    if anchor is None:
        return None
    today = _now_local().date()
    return anchor.year == today.year and anchor <= today


# Date.IsIn{Current,Next,Previous}{Day,Week,Month,Quarter,Year}[N] - see the
# "Relative-period predicates" engine above. Month/Year "Current" already
# exist (pre-existing, untouched); Day/Week/Quarter "Current" are new.
_DATE_ISIN_CURRENT_UNITS: tuple[tuple[str, str], ...] = (
    ("Day", "day"),
    ("Week", "week"),
    ("Quarter", "quarter"),
)
_DATE_ISIN_ALL_UNITS: tuple[tuple[str, str], ...] = (
    ("Day", "day"),
    ("Week", "week"),
    ("Month", "month"),
    ("Quarter", "quarter"),
    ("Year", "year"),
)
_DATE_ISIN_BUILTINS: dict[str, Any] = {}
for _label, _unit in _DATE_ISIN_CURRENT_UNITS:
    _name = f"Date.IsInCurrent{_label}"
    _DATE_ISIN_BUILTINS[_name] = _make_is_in(
        _name, _period_anchor_date, "date", _unit, 0, 0
    )
for _label, _unit in _DATE_ISIN_ALL_UNITS:
    _next_name = f"Date.IsInNext{_label}"
    _DATE_ISIN_BUILTINS[_next_name] = _make_is_in(
        _next_name, _period_anchor_date, "date", _unit, 1, 1
    )
    _nextn_name = f"Date.IsInNextN{_label}s"
    _DATE_ISIN_BUILTINS[_nextn_name] = _make_is_in_n(
        _nextn_name, _period_anchor_date, "date", _unit, 1
    )
    _prev_name = f"Date.IsInPrevious{_label}"
    _DATE_ISIN_BUILTINS[_prev_name] = _make_is_in(
        _prev_name, _period_anchor_date, "date", _unit, -1, -1
    )
    _prevn_name = f"Date.IsInPreviousN{_label}s"
    _DATE_ISIN_BUILTINS[_prevn_name] = _make_is_in_n(
        _prevn_name, _period_anchor_date, "date", _unit, -1
    )
del _label, _unit, _name, _next_name, _nextn_name, _prev_name, _prevn_name


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
    if len(fmt) == 1:
        return _resolve_standard_format("DateTime.ToText", dt, fmt)
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


# Windows FILETIME epoch: 100-nanosecond ticks since 1601-01-01 00:00 UTC.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


def _datetime_from_filetime_ticks(ticks: int | float) -> datetime:
    """UTC instant of a FILETIME tick count, as an AWARE `datetime`.

    Shared by DateTime.FromFileTime (drops tzinfo after localising) and
    DateTimeZone.FromFileTime (keeps it) - both documented to "convert it
    to the local time zone", so both go through `.astimezone()`.
    `.astimezone()` with no argument uses the HOST's own timezone database
    (Python stdlib, via the OS) to resolve the correct offset for that
    exact historical/future instant, including DST - it is not a guessed
    offset, so this is not the IANA/Windows-DB-less-guess Rule 1 forbids.
    It IS still host-dependent (a different machine's local zone gives a
    different wall-clock reading for the identical input), exactly like
    this module's existing DateTime.LocalNow/FixedLocalNow gap (module
    docstring point 7) - no test may assert an exact wall-clock value here,
    only an independent same-host recomputation.
    """
    return (_FILETIME_EPOCH + timedelta(microseconds=ticks / 10)).astimezone()


def _datetime_from_file_time(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.FromFileTime", args, 1)
    if args[0] is None:
        return None
    ticks = _require_number(args[0])
    return _datetime_from_filetime_ticks(ticks).replace(tzinfo=None)


def _seconds_field(second: int, microsecond: int) -> int | float:
    """The `Second` field of a *.ToRecord result.

    Every fetched ToRecord doc example uses a whole-number Second with no
    fractional part shown, so a whole second stays a plain int (matching
    those examples exactly). A fractional second is not silently dropped
    either way - no doc example determines what to name a fractional
    field, and inventing one (e.g. "Millisecond") would be a fact this
    module cannot verify - so the fraction is folded into `Second` as a
    float instead of being lost. Documented CHOICE, pinned by a test.
    """
    if microsecond == 0:
        return second
    return second + microsecond / 1_000_000


def _datetime_to_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTime.ToRecord", args, 1)
    dt = _coerce_datetime_like("DateTime.ToRecord", args[0])
    if dt is None:
        return None
    return {
        "Year": dt.year,
        "Month": dt.month,
        "Day": dt.day,
        "Hour": dt.hour,
        "Minute": dt.minute,
        "Second": _seconds_field(dt.second, dt.microsecond),
    }


# DateTime.IsIn{Current,Next,Previous}{Hour,Minute,Second}[N] - same engine
# as the Date.IsIn* family above, at datetime (not date) resolution. Docs
# for this family say "A datetime, or datetimezone value" (no bare
# `date`), so `_period_anchor_datetime` (via `_coerce_datetime_like`) is
# used rather than `_period_anchor_date`.
_TIME_ISIN_UNITS: tuple[tuple[str, str], ...] = (
    ("Hour", "hour"),
    ("Minute", "minute"),
    ("Second", "second"),
)
_TIME_ISIN_BUILTINS: dict[str, Any] = {}
for _label, _unit in _TIME_ISIN_UNITS:
    _cur_name = f"DateTime.IsInCurrent{_label}"
    _TIME_ISIN_BUILTINS[_cur_name] = _make_is_in(
        _cur_name, _period_anchor_datetime, "datetime", _unit, 0, 0
    )
    _next_name = f"DateTime.IsInNext{_label}"
    _TIME_ISIN_BUILTINS[_next_name] = _make_is_in(
        _next_name, _period_anchor_datetime, "datetime", _unit, 1, 1
    )
    _nextn_name = f"DateTime.IsInNextN{_label}s"
    _TIME_ISIN_BUILTINS[_nextn_name] = _make_is_in_n(
        _nextn_name, _period_anchor_datetime, "datetime", _unit, 1
    )
    _prev_name = f"DateTime.IsInPrevious{_label}"
    _TIME_ISIN_BUILTINS[_prev_name] = _make_is_in(
        _prev_name, _period_anchor_datetime, "datetime", _unit, -1, -1
    )
    _prevn_name = f"DateTime.IsInPreviousN{_label}s"
    _TIME_ISIN_BUILTINS[_prevn_name] = _make_is_in_n(
        _prevn_name, _period_anchor_datetime, "datetime", _unit, -1
    )
del _label, _unit, _cur_name, _next_name, _nextn_name, _prev_name, _prevn_name


# --------------------------------------------------------------------------
# DateTimeZone.*
#
# A `datetimezone` IS a `datetime` with `tzinfo` set (module docstring
# point 4) - there is no separate wrapper type, so every function below
# just reads/builds/rewrites `tzinfo` on the same Python `datetime` the
# rest of this module already uses.
# --------------------------------------------------------------------------


def _require_aware_datetime(name: str, value: Any) -> datetime | None:
    """Strict coercion for DateTimeZone.* functions declared `datetimezone`
    (not `any`) in the docs - RemoveZone, SwitchZone, ZoneHours,
    ZoneMinutes, ToRecord, ToText all use this. Unlike the permissive
    `_coerce_date_like`-family accessors (Date.Year etc., all declared
    `any` and documented to accept text/number/date/datetime too), this
    module reads a strict declared type as meaning exactly what it says:
    an already-aware value, nothing coerced from text/number/date, and a
    NAIVE datetime is a type error rather than a silently-guessed zone
    (unlike ToLocal/ToUtc below, whose OWN docs explicitly say what to do
    with a naive value).
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise EvalError(f"{name}: expected a datetimezone, got {_type_name(value)}")
    if value.tzinfo is None:
        raise EvalError(f"{name}: expected a datetimezone, got a datetime with no zone")
    return value


def _coerce_maybe_aware_datetime(name: str, value: Any) -> datetime | None:
    """For ToLocal/ToUtc: accepts a naive OR aware `datetime`, per their
    own docs ("if dateTimeZone does not have a timezone component, the
    [...] timezone information is added")."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise EvalError(f"{name}: expected a datetimezone, got {_type_name(value)}")
    return value


def _zone_offset_components(offset: timedelta) -> tuple[int, int]:
    """(ZoneHours, ZoneMinutes) decomposition of a UTC offset.

    Both components carry the offset's sign (matching how the `#datetimezone`
    literal and DateTimeZone.SwitchZone's own two-argument shape take
    signed hours and signed minutes separately, e.g.
    SwitchZone(..., 0, -30) -> ZoneMinutes = -30) - a documented CHOICE
    where no fetched example has a non-zero-hours negative offset to
    verify against directly; pinned by a round-trip test instead
    (construct #datetimezone(...,-7,-30), read ZoneHours/ZoneMinutes back).
    """
    total_minutes = round(offset.total_seconds() / 60)
    sign = -1 if total_minutes < 0 else 1
    hours, minutes = divmod(abs(total_minutes), 60)
    return sign * hours, sign * minutes


def _datetimezone_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.From", args, 1, 2)
    if len(args) == 2:
        _check_invariant_culture("DateTimeZone.From", args[1])
    value = args[0]
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.astimezone()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).astimezone()
    if isinstance(value, time):
        # OLE Automation Date 0 = 1899-12-30 (module docstring point 5 /
        # `_OLE_EPOCH`) - the real docs spell this out explicitly for this
        # exact case.
        return datetime.combine(_OLE_EPOCH, value).astimezone()
    if isinstance(value, bool):
        raise EvalError("DateTimeZone.From: expected a value, got logical")
    if isinstance(value, (int, float)):
        return _datetime_from_ole_serial("DateTimeZone.From", value).astimezone()
    if isinstance(value, str):
        return _datetimezone_from_text([value], ctx)
    raise EvalError(f"DateTimeZone.From: expected a value, got {_type_name(value)}")


def _resolve_from_text_options(name: str, rest: list[Any]) -> tuple[str | None, Any]:
    """(format, culture) for X.FromText's optional 2nd positional arg.

    Mirrors `_resolve_to_text_options`'s [Format=.., Culture=..]-record /
    bare-text duality, but for PARSING. A non-null Format means custom or
    standard format-string PARSING (the reverse of `_format_custom`),
    which this module does not implement - the pre-existing sibling
    Date/DateTime/Time/Duration.FromText functions don't implement it
    either (their 2nd argument is only ever read as a bare culture
    string), so refusing here keeps this the one place in the file that is
    honest about the gap rather than quietly ignoring the option.
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
            culture = first
        else:
            raise EvalError(
                f"{name}: options must be text or a [Format = ...] record, "
                f"got {_type_name(first)}"
            )
    return fmt, culture


def _datetimezone_from_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.FromText", args, 1, 2)
    text = args[0]
    if text is None:
        return None
    if not isinstance(text, str):
        raise EvalError(f"DateTimeZone.FromText: expected text, got {_type_name(text)}")
    fmt, culture = _resolve_from_text_options("DateTimeZone.FromText", args[1:])
    _shared_check_culture("DateTimeZone.FromText", culture, "culture-specific parsing")
    if fmt is not None:
        raise UnsupportedError(
            "DateTimeZone.FromText: a custom/standard Format string for "
            "parsing (as opposed to formatting) is not implemented"
        )
    stripped = text.strip()
    try:
        dt = datetime.fromisoformat(stripped)
    except ValueError as error:
        raise EvalError(
            f"DateTimeZone.FromText: not a valid ISO datetimezone: {text!r}"
        ) from error
    if dt.tzinfo is None:
        raise EvalError(
            f"DateTimeZone.FromText: text has no time zone offset: {text!r}"
        )
    return dt


def _datetimezone_from_file_time(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.FromFileTime", args, 1)
    if args[0] is None:
        return None
    ticks = _require_number(args[0])
    return _datetime_from_filetime_ticks(ticks)


def _datetimezone_local_now(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.LocalNow", args, 0)
    return datetime.now().astimezone()


def _datetimezone_fixed_local_now(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.FixedLocalNow", args, 0)
    # Not memoised within one evaluate() call - same documented gap as
    # DateTime.FixedLocalNow (module docstring point 7).
    return datetime.now().astimezone()


def _datetimezone_utc_now(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.UtcNow", args, 0)
    return datetime.now(UTC)


def _datetimezone_fixed_utc_now(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.FixedUtcNow", args, 0)
    return datetime.now(UTC)


def _datetimezone_remove_zone(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.RemoveZone", args, 1)
    dt = _require_aware_datetime("DateTimeZone.RemoveZone", args[0])
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


def _datetimezone_switch_zone(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.SwitchZone", args, 2, 3)
    dt = _require_aware_datetime("DateTimeZone.SwitchZone", args[0])
    if dt is None:
        return None
    hours = _require_number(args[1])
    minutes = _require_number(args[2]) if len(args) == 3 and args[2] is not None else 0
    try:
        tz = timezone(timedelta(hours=hours, minutes=minutes))
    except ValueError as error:
        raise EvalError(
            f"DateTimeZone.SwitchZone: invalid time zone offset: {error}"
        ) from error
    return dt.astimezone(tz)


def _datetimezone_to_local(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.ToLocal", args, 1)
    dt = _coerce_maybe_aware_datetime("DateTimeZone.ToLocal", args[0])
    if dt is None:
        return None
    # `datetime.astimezone()` with no argument does exactly what the docs
    # describe for both cases: a naive value is presumed to already BE
    # local wall-clock time, so it is only labelled (no shift); an aware
    # value is genuinely converted (shifted) to the local offset.
    return dt.astimezone()


def _datetimezone_to_utc(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.ToUtc", args, 1)
    dt = _coerce_maybe_aware_datetime("DateTimeZone.ToUtc", args[0])
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Docs: "the UTC timezone information is added" for a value with no
        # zone - added, not converted, so (unlike ToLocal) this must NOT
        # treat the naive wall-clock as local-then-convert; it is labelled
        # UTC as-is, matching DateTime.AddZone's own "no shift" behaviour
        # for a naive value (module docstring point 4).
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _datetimezone_to_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.ToRecord", args, 1)
    dt = _require_aware_datetime("DateTimeZone.ToRecord", args[0])
    if dt is None:
        return None
    offset = dt.utcoffset()
    assert offset is not None  # tzinfo is set; utcoffset() cannot be None here
    zone_hours, zone_minutes = _zone_offset_components(offset)
    return {
        "Year": dt.year,
        "Month": dt.month,
        "Day": dt.day,
        "Hour": dt.hour,
        "Minute": dt.minute,
        "Second": _seconds_field(dt.second, dt.microsecond),
        "ZoneHours": zone_hours,
        "ZoneMinutes": zone_minutes,
    }


def _datetimezone_to_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.ToText", args, 1, 3)
    dt = _require_aware_datetime("DateTimeZone.ToText", args[0])
    if dt is None:
        return None
    fmt, culture = _resolve_to_text_options("DateTimeZone.ToText", args[1:])
    _check_invariant_culture("DateTimeZone.ToText", culture)
    if fmt is None:
        # Real PQ's culture-formatted default ("12/31/2010 1:30:25 AM
        # +02:00") is not reproduced - this module's other three ToText
        # siblings (Date/DateTime/Time) already made the same simplifying
        # choice (an unambiguous invariant ISO default instead of a
        # locale-formatted one); `.isoformat()` already appends the offset
        # for an aware value, so this stays consistent with them.
        return dt.isoformat(sep=" ")
    if len(fmt) == 1:
        return _resolve_standard_format("DateTimeZone.ToText", dt, fmt)
    return _format_custom("DateTimeZone.ToText", dt, fmt)


def _datetimezone_zone_hours(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.ZoneHours", args, 1)
    dt = _require_aware_datetime("DateTimeZone.ZoneHours", args[0])
    if dt is None:
        return None
    offset = dt.utcoffset()
    assert offset is not None
    return _zone_offset_components(offset)[0]


def _datetimezone_zone_minutes(args: list[Any], ctx: _Ctx) -> Any:
    _arity("DateTimeZone.ZoneMinutes", args, 1)
    dt = _require_aware_datetime("DateTimeZone.ZoneMinutes", args[0])
    if dt is None:
        return None
    offset = dt.utcoffset()
    assert offset is not None
    return _zone_offset_components(offset)[1]


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


def _duration_to_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Duration.ToRecord", args, 1)
    td = _coerce_duration_like("Duration.ToRecord", args[0])
    if td is None:
        return None
    sign, days, hours, minutes, seconds, microseconds = _duration_components(td)
    return {
        "Days": sign * days,
        "Hours": sign * hours,
        "Minutes": sign * minutes,
        "Seconds": sign * _seconds_field(seconds, microseconds),
    }


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
    if len(fmt) == 1:
        return _resolve_standard_format("Time.ToText", t, fmt)
    return _format_custom("Time.ToText", t, fmt)


def _coerce_time_or_datetime(name: str, value: Any) -> time | datetime | None:
    """Coercion for Time.StartOfHour/EndOfHour only.

    Every other Time.* accessor uses `_coerce_time_like`, which discards
    any date component (`.time()`) - correct for Hour/Minute/Second, but
    wrong here: the docs show a `datetime`/`datetimezone` argument coming
    back with its DATE (and, per Time.EndOfHour's docs, its time zone)
    still attached, only the hour/minute/second/microsecond fields
    changed. So a `datetime`/`time` value passes through unchanged instead
    of being narrowed to a bare `time` first.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, time):
        return value
    if isinstance(value, bool):
        raise EvalError(f"{name}: expected a time, got logical")
    if isinstance(value, (int, float)):
        return _time_from_day_fraction(value)
    if isinstance(value, str):
        return _parse_iso_time(name, value)
    raise EvalError(f"{name}: expected a time, got {_type_name(value)}")


def _time_start_of_hour(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Time.StartOfHour", args, 1)
    value = _coerce_time_or_datetime("Time.StartOfHour", args[0])
    if value is None:
        return None
    return value.replace(minute=0, second=0, microsecond=0)


def _time_end_of_hour(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Time.EndOfHour", args, 1)
    value = _coerce_time_or_datetime("Time.EndOfHour", args[0])
    if value is None:
        return None
    # See `_end_like`'s docstring: 999999 microseconds is this module's
    # representable ceiling, not a rounding of the docs' 23:59:59.9999999.
    return value.replace(minute=59, second=59, microsecond=999_999)


def _time_to_record(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Time.ToRecord", args, 1)
    t = _coerce_time_like("Time.ToRecord", args[0])
    if t is None:
        return None
    return {
        "Hour": t.hour,
        "Minute": t.minute,
        "Second": _seconds_field(t.second, t.microsecond),
    }


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
    "Date.FromText": _from_text("Date.FromText", _date_from),
    "DateTime.FromText": _from_text("DateTime.FromText", _datetime_from),
    "Time.FromText": _from_text("Time.FromText", _time_from),
    "Duration.FromText": _from_text("Duration.FromText", _duration_from),
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
    "Date.AddQuarters": _date_add_quarters,
    "Date.DaysInMonth": _date_days_in_month,
    "Date.StartOfDay": _date_start_of_day,
    "Date.EndOfDay": _date_end_of_day,
    "Date.StartOfQuarter": _date_start_of_quarter,
    "Date.EndOfQuarter": _date_end_of_quarter,
    "Date.IsLeapYear": _date_is_leap_year,
    "Date.IsInYearToDate": _date_is_in_year_to_date,
    "Date.ToRecord": _date_to_record,
    "Date.WeekOfMonth": _date_week_of_month,
    **_DATE_ISIN_BUILTINS,
    "DateTime.From": _datetime_from,
    "DateTime.Date": _datetime_date,
    "DateTime.Time": _datetime_time,
    "DateTime.LocalNow": _datetime_local_now,
    "DateTime.FixedLocalNow": _datetime_fixed_local_now,
    "DateTime.ToText": _datetime_to_text,
    "DateTime.AddZone": _datetime_add_zone,
    "DateTime.FromFileTime": _datetime_from_file_time,
    "DateTime.ToRecord": _datetime_to_record,
    **_TIME_ISIN_BUILTINS,
    "DateTimeZone.From": _datetimezone_from,
    "DateTimeZone.FromText": _datetimezone_from_text,
    "DateTimeZone.FromFileTime": _datetimezone_from_file_time,
    "DateTimeZone.LocalNow": _datetimezone_local_now,
    "DateTimeZone.FixedLocalNow": _datetimezone_fixed_local_now,
    "DateTimeZone.UtcNow": _datetimezone_utc_now,
    "DateTimeZone.FixedUtcNow": _datetimezone_fixed_utc_now,
    "DateTimeZone.RemoveZone": _datetimezone_remove_zone,
    "DateTimeZone.SwitchZone": _datetimezone_switch_zone,
    "DateTimeZone.ToLocal": _datetimezone_to_local,
    "DateTimeZone.ToUtc": _datetimezone_to_utc,
    "DateTimeZone.ToRecord": _datetimezone_to_record,
    "DateTimeZone.ToText": _datetimezone_to_text,
    "DateTimeZone.ZoneHours": _datetimezone_zone_hours,
    "DateTimeZone.ZoneMinutes": _datetimezone_zone_minutes,
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
    "Duration.ToRecord": _duration_to_record,
    "Time.From": _time_from,
    "Time.Hour": _time_hour,
    "Time.Minute": _time_minute,
    "Time.Second": _time_second,
    "Time.ToText": _time_to_text,
    "Time.StartOfHour": _time_start_of_hour,
    "Time.EndOfHour": _time_end_of_hour,
    "Time.ToRecord": _time_to_record,
    **_DAY_ENUM,
}
