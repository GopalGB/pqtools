"""``Text.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.
"""

from __future__ import annotations

import datetime
import re
import uuid as _uuid
from typing import TYPE_CHECKING, Any

from ._connectors import _CODE_PAGES, _DEFAULT_ENCODING
from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _check_invariant_culture,
    _format_number,
    _require_int,
    _require_list,
    _require_str,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _consume_budget(ctx: _Ctx, count: int) -> None:
    """Charge `count` steps against ctx.budget before an operation whose
    cost scales with a caller-supplied count (Text.PadStart/PadEnd/Repeat),
    so a huge count fails fast with EvalError instead of allocating an
    unbounded string. See PRD-0.5.0-builtins.md correctness rule 6.
    """
    for _ in range(count):
        ctx.budget.tick()


def _text_from(args: list[Any], ctx: _Ctx) -> Any:
    # Text.From(value as any, optional culture as nullable text) as
    # nullable text. "The value can be a number, date, time, datetime,
    # datetimezone, logical, duration, or binary value" (About, verbatim) -
    # every one of those is handled below; culture is validated ONCE up
    # front so every branch's refusal reads "Text.From: ..." rather than
    # the name of whichever *.ToText/Binary.ToText it delegates to (all of
    # which would reject the exact same non-invariant culture anyway).
    _arity("Text.From", args, 1, 2)
    value = args[0]
    culture = args[1] if len(args) == 2 else None
    _check_invariant_culture("Text.From", culture, "culture-specific text formatting")
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return _format_number(value)
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        # Example 4 on Text.From's own page: converting
        # Binary.FromText("10FF", BinaryEncoding.Hex) back with Text.From
        # gives "EP8=" - base64, Binary.ToText's own default encoding
        # (verified: base64.b64encode(bytes([0x10, 0xFF])) == b"EP8=").
        # Delegating to Binary.ToText rather than re-encoding here is the
        # same "one formatter per concept" reasoning the temporal branches
        # below give for _datetime.py - imported lazily, the pattern
        # _table.py's Table.AddColumn documents (the common non-binary
        # path never pays for the import).
        from ._connectors import _binary_to_text

        return _binary_to_text([value], ctx)
    if isinstance(value, datetime.datetime):
        # datetime/datetimezone - checked before `datetime.date` below
        # because datetime.datetime IS a datetime.date subclass (the same
        # ordering trap _shared._type_name/_m_equal already document).
        from ._datetime import _datetime_to_text, _datetimezone_to_text

        if value.tzinfo is not None:
            # No worked example anywhere in the M reference shows
            # Text.From on a datetimezone value. DOCUMENTED CHOICE, not a
            # verified fact: agree with DateTimeZone.ToText's own bare
            # (no format argument) default rather than guess a
            # locale "general" pattern nobody has pinned - that default
            # is itself a deliberate, already-documented simplification
            # (see _datetime.py's DateTimeZone.ToText: an unambiguous
            # invariant ISO string that keeps the offset, chosen over a
            # locale-formatted one). Pinned by
            # test_text_format.py::test_text_from_datetimezone_has_no_ms_grounding.
            return _datetimezone_to_text([value], ctx)
        # GROUNDED: Text.From's own Example 2 -
        # Text.From(#datetime(2024, 6, 24, 14, 32, 22)) ->
        # "6/24/2024 2:32:22 PM". That is exactly DateTime.ToText's "G"
        # (general date/long time) standard pattern - verified by running
        # DateTime.ToText(#datetime(2024, 6, 24, 14, 32, 22), "G") through
        # this package's own evaluator (`pq eval`) and getting the
        # identical string back, not assumed from .NET documentation.
        return _datetime_to_text([value, "G"], ctx)
    if isinstance(value, datetime.date):
        # GROUNDED: Text.Format's own Example 2 (this module's Text.Format
        # is graded against it) renders #date(2015, 3, 10) as "3/10/2015",
        # which is Date.ToText's "d" (short date) standard pattern -
        # verified the same way as the datetime branch above:
        # Date.ToText(#date(2015, 3, 10), "d") reproduces "3/10/2015"
        # exactly.
        from ._datetime import _date_to_text

        return _date_to_text([value, "d"], ctx)
    if isinstance(value, datetime.time):
        # No worked example shows Text.From on a bare time value either.
        # DOCUMENTED CHOICE, same reasoning as datetimezone above: agree
        # with Time.ToText's own bare (ISO) default rather than guess an
        # unpinned locale pattern.
        from ._datetime import _time_to_text

        return _time_to_text([value], ctx)
    if isinstance(value, datetime.timedelta):
        # GROUNDED: Text.Format's own Example 2 renders
        # #duration(0, 0, 54, 40) as "00:54:40", which is exactly
        # Duration.ToText's bare default - verified directly:
        # Duration.ToText(#duration(0, 0, 54, 40)) reproduces "00:54:40".
        # Duration.ToText has no culture parameter at all (its Syntax
        # block is `(duration, optional format)` - the `format` argument
        # is documented "Deprecated, will raise an error if not null"), so
        # the upfront _check_invariant_culture call above is the only
        # culture validation this branch gets; nothing is passed through.
        from ._datetime import _duration_to_text

        return _duration_to_text([value], ctx)
    raise EvalError(f"Text.From: unsupported value type: {_type_name(value)}")


# #{0}/#{1}/... index a LIST argument; #[name] indexes a RECORD argument -
# Text.Format's own two worked examples, one of each shape. The character
# class excludes `[`/`]` rather than matching `\w+`, since a real M record
# field name can contain spaces (Example 2's own #[distance]/#[city] are
# plain identifiers, but nothing on the page says field names with spaces
# are excluded, and #"Company ID"-style names appear elsewhere in this
# reference).
_FORMAT_TOKEN_RE = re.compile(r"#\{(\d+)\}|#\[([^\[\]]+)\]")


def _text_format(args: list[Any], ctx: _Ctx) -> Any:
    # Text.Format(formatString as text, arguments as any, optional culture
    # as nullable text) as text. "Returns formatted text that is created
    # by applying arguments from a list or record to a format string" -
    # About, verbatim. Each substituted value is rendered by delegating to
    # Text.From with the same culture, rather than a second formatter:
    # Example 2's own output ("3/10/2015" for the date, "00:54:40" for the
    # duration) is byte-for-byte what Text.From already produces for those
    # same values under "en-US" (see _text_from above) - two independent
    # renderers for one concept is the exact bug class this package keeps
    # finding (module docstring cross-references throughout this file).
    _arity("Text.Format", args, 2, 3)
    format_string = _require_str(args[0])
    arguments = args[1]
    culture = args[2] if len(args) == 3 else None
    _check_invariant_culture("Text.Format", culture, "culture-specific text formatting")
    if not isinstance(arguments, (list, dict)):
        raise EvalError(
            "Text.Format: arguments must be a list (for #{N} placeholders) "
            f"or a record (for #[name] placeholders), got {_type_name(arguments)}"
        )

    def substitute(match: re.Match[str]) -> str:
        index_token, name_token = match.group(1), match.group(2)
        if index_token is not None:
            if not isinstance(arguments, list):
                raise EvalError(
                    f"Text.Format: format string references #{{{index_token}}} "
                    "(a list index) but arguments is a record, not a list"
                )
            index = int(index_token)
            # Microsoft's page does not say what an out-of-range index
            # does - no example exercises it, and guessing "" (the way a
            # naive str.format-style renderer would) is exactly the
            # plausible-wrong-answer failure this package refuses to
            # produce elsewhere (see _text_infer_number_type above for the
            # same call). This fails loudly instead, naming the index and
            # the list's real length. Pinned by test_text_format.py.
            if index >= len(arguments):
                raise EvalError(
                    f"Text.Format: argument index {index} is out of range "
                    f"for a list of {len(arguments)} value(s)"
                )
            value = arguments[index]
        else:
            assert name_token is not None  # one alternative always matches
            if not isinstance(arguments, dict):
                raise EvalError(
                    f"Text.Format: format string references #[{name_token}] "
                    "(a record field) but arguments is a list, not a record"
                )
            # Same "Microsoft does not say" gap as the index case above,
            # same choice: name the missing field rather than substitute
            # "". Pinned by test_text_format.py.
            if name_token not in arguments:
                raise EvalError(
                    f"Text.Format: field {name_token!r} not found in the "
                    f"arguments record (available: {sorted(arguments)})"
                )
            value = arguments[name_token]
        rendered = _text_from([value, culture], ctx)
        if rendered is None:
            raise EvalError(
                "Text.Format: cannot format a null value into text "
                f"({match.group(0)} resolved to null)"
            )
        # _text_from is declared `-> Any` like every builtin in this
        # registry; every one of its non-None branches already returns a
        # str, so this is a defensive narrowing (satisfies mypy --strict
        # and catches a future _text_from regression here rather than
        # silently embedding a wrong-typed value in the result string).
        return _require_str(rendered)

    return _FORMAT_TOKEN_RE.sub(substitute, format_string)


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
    # Text.Contains(text as nullable text, substring as text, optional
    # comparer as nullable function) as nullable logical - `text` IS
    # nullable in the real signature (verified against MS docs: "If the
    # first argument is null, this function returns null"), which the
    # prior 2-arg-only form here never propagated - fixed alongside adding
    # `comparer` since both are the same signature correction.
    _arity("Text.Contains", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    substring = _require_str(args[1])
    if len(args) == 3 and args[2] is not None:
        if _resolve_text_comparer(args[2], "Text.Contains"):
            return re.search(re.escape(substring), text, re.IGNORECASE) is not None
    return substring in text


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


def _char_set(value: Any, fn_name: str) -> set[str]:
    """Resolve a Text.Select/Text.Remove/Text.PositionOfAny character list.

    Real PQ accepts a text value (its characters) or a list of characters.
    It also accepts a list of ranges (``{"a".."z"}``), but the evaluator
    does not implement ``RangeExpression`` at all (see
    ``_SIMPLE_UNSUPPORTED`` in evaluate.py) - a query using that syntax
    already fails before this function is ever called - so only the two
    reachable shapes are handled here.
    """
    if isinstance(value, str):
        return set(value)
    if isinstance(value, list):
        chars: set[str] = set()
        for item in value:
            item_str = _require_str(item)
            if len(item_str) != 1:
                raise UnsupportedError(
                    f"{fn_name}: multi-character list item {item_str!r} "
                    "(character ranges are unsupported)"
                )
            chars.add(item_str)
        return chars
    raise EvalError(f"{fn_name}: expected text or a list of characters")


def _text_pad_start(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.PadStart", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Text.PadStart: count must not be negative")
    character = " "
    if len(args) == 3 and args[2] is not None:
        character = _require_str(args[2])
        if len(character) != 1:
            raise EvalError("Text.PadStart: character must be a single character")
    if len(text) >= count:
        return text
    _consume_budget(ctx, count - len(text))
    return character * (count - len(text)) + text


def _text_pad_end(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.PadEnd", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Text.PadEnd: count must not be negative")
    character = " "
    if len(args) == 3 and args[2] is not None:
        character = _require_str(args[2])
        if len(character) != 1:
            raise EvalError("Text.PadEnd: character must be a single character")
    if len(text) >= count:
        return text
    _consume_budget(ctx, count - len(text))
    return text + character * (count - len(text))


def _text_middle(args: list[Any], ctx: _Ctx) -> Any:
    # Text.Middle(text as nullable text, start as number, optional count as
    # nullable number) as nullable text - "returns count characters, or
    # through the end of text, at the offset start". Python slicing already
    # clamps an over-long count/start (verified against MS docs example 2:
    # Text.Middle("Hello World", 6, 20) = "World").
    _arity("Text.Middle", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    start = _require_int(args[1])
    if start < 0:
        raise EvalError("Text.Middle: start must not be negative")
    if len(args) == 3 and args[2] is not None:
        count = _require_int(args[2])
        if count < 0:
            raise EvalError("Text.Middle: count must not be negative")
        return text[start : start + count]
    return text[start:]


def _text_before_delimiter(args: list[Any], ctx: _Ctx) -> Any:
    # Text.BeforeDelimiter(text as nullable text, delimiter as text,
    # optional index as any) as any. Trap (verified against real PQ):
    # when the delimiter is NOT found, this returns the WHOLE original
    # text - it does not throw and does not return "".
    _arity("Text.BeforeDelimiter", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    delimiter = _require_str(args[1])
    if not delimiter:
        raise EvalError("Text.BeforeDelimiter: delimiter must not be empty")
    index = 0
    if len(args) == 3 and args[2] is not None:
        if isinstance(args[2], list):
            raise UnsupportedError(
                "Text.BeforeDelimiter: list-form index (RelativePosition)"
            )
        index = _require_int(args[2])
        if index < 0:
            raise EvalError("Text.BeforeDelimiter: index must not be negative")
    pos = -1
    search_from = 0
    for _ in range(index + 1):
        ctx.budget.tick()
        pos = text.find(delimiter, search_from)
        if pos == -1:
            return text
        search_from = pos + len(delimiter)
    return text[:pos]


def _text_after_delimiter(args: list[Any], ctx: _Ctx) -> Any:
    # Text.AfterDelimiter(...) - trap (verified against real PQ): unlike
    # Text.BeforeDelimiter, when the delimiter is NOT found this returns
    # "" (empty text), not the whole text. The two are NOT mirror images.
    _arity("Text.AfterDelimiter", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    delimiter = _require_str(args[1])
    if not delimiter:
        raise EvalError("Text.AfterDelimiter: delimiter must not be empty")
    index = 0
    if len(args) == 3 and args[2] is not None:
        if isinstance(args[2], list):
            raise UnsupportedError(
                "Text.AfterDelimiter: list-form index (RelativePosition)"
            )
        index = _require_int(args[2])
        if index < 0:
            raise EvalError("Text.AfterDelimiter: index must not be negative")
    pos = -1
    search_from = 0
    for _ in range(index + 1):
        ctx.budget.tick()
        pos = text.find(delimiter, search_from)
        if pos == -1:
            return ""
        search_from = pos + len(delimiter)
    return text[pos + len(delimiter) :]


def _text_between_delimiters(args: list[Any], ctx: _Ctx) -> Any:
    # Text.BetweenDelimiters(text as nullable text, startDelimiter as text,
    # endDelimiter as text, optional startIndex as any,
    # optional endIndex as any) as any. Verified against the real PQ docs'
    # own worked example (startIndex=1, endIndex=0 on "111 (222) 333 (444)"
    # -> "444"). If either delimiter isn't found, returns "" (verified).
    _arity("Text.BetweenDelimiters", args, 3, 5)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    start_delim = _require_str(args[1])
    end_delim = _require_str(args[2])
    if not start_delim or not end_delim:
        raise EvalError("Text.BetweenDelimiters: delimiters must not be empty")
    start_index = 0
    if len(args) >= 4 and args[3] is not None:
        if isinstance(args[3], list):
            raise UnsupportedError(
                "Text.BetweenDelimiters: list-form startIndex (RelativePosition)"
            )
        start_index = _require_int(args[3])
        if start_index < 0:
            raise EvalError("Text.BetweenDelimiters: startIndex must not be negative")
    end_index = 0
    if len(args) == 5 and args[4] is not None:
        if isinstance(args[4], list):
            raise UnsupportedError(
                "Text.BetweenDelimiters: list-form endIndex (RelativePosition)"
            )
        end_index = _require_int(args[4])
        if end_index < 0:
            raise EvalError("Text.BetweenDelimiters: endIndex must not be negative")
    pos = -1
    search_from = 0
    for _ in range(start_index + 1):
        ctx.budget.tick()
        pos = text.find(start_delim, search_from)
        if pos == -1:
            return ""
        search_from = pos + len(start_delim)
    start_of_between = search_from
    end_pos = -1
    for _ in range(end_index + 1):
        ctx.budget.tick()
        end_pos = text.find(end_delim, search_from)
        if end_pos == -1:
            return ""
        search_from = end_pos + len(end_delim)
    return text[start_of_between:end_pos]


def _text_select(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Select", args, 2)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    chars = _char_set(args[1], "Text.Select")
    return "".join(c for c in text if c in chars)


def _text_remove(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Remove", args, 2)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    chars = _char_set(args[1], "Text.Remove")
    return "".join(c for c in text if c not in chars)


def _text_repeat(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Repeat", args, 2)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Text.Repeat: count must not be negative")
    _consume_budget(ctx, count)
    return text * count


def _text_reverse(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Reverse", args, 1)
    text = args[0]
    if text is None:
        return None
    return _require_str(text)[::-1]


# --------------------------------------------------------------------------
# Comparer.* - the `comparer` argument Text.Contains/StartsWith/EndsWith/
# PositionOf accept. Comparer.Ordinal(x, y)/Comparer.OrdinalIgnoreCase(x, y)
# are themselves invokable 2-argument comparer functions in real M (verified
# against MS docs: `Comparer.OrdinalIgnoreCase("Abc", "abc")` -> `0`), not
# opaque enum tags - registered here as ordinary builtins the same way any
# other function is. `m_is_builtin_comparer` is a plain attribute tag (not an
# M-visible name) so List.*'s equationCriteria handling in the sibling
# _list.py module can recognise these two specific values without an
# import across family modules - see builtins/__init__.py's docstring on
# why family modules stay independent of each other.
# --------------------------------------------------------------------------


def _ordinal_sign(left: str, right: str) -> int:
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def _non_text_compare(name: str, left: Any, right: Any) -> int:
    """Ordering for the values an "Ordinal" comparison says nothing about.

    These were text-only, on the stated grounds that every verified example
    compared text and there was "no confirmed non-text behaviour to
    implement". The evidence has since turned up: Table.RemoveMatchingRows
    Example 2 passes Comparer.OrdinalIgnoreCase as the equation criteria for
    a table whose columns are `OrderID = number`, `Product = text`,
    `Quantity = number`, and the page's documented output has the matching
    row REMOVED - which requires the comparer to accept the two numbers. The
    signature agrees: `(x as any, y as any)`, not `(x as text, ...)`.

    "Ordinal" describes how TEXT is compared; for anything else there is no
    codepoint order to take and nothing to case-fold, so these fall back to
    M's own default ordering - the same rule Value.Compare implements.
    """
    # Lazy, for the reason Table.AddColumn documents: _type imports nothing
    # from here, and the common text path should not pay for the import.
    from ._type import _default_compare

    try:
        return _default_compare(left, right)
    except UnsupportedError as error:
        raise UnsupportedError(f"{name}: {error}") from error


def _compare_any(name: str, left: Any, right: Any, *, fold: bool) -> int:
    if isinstance(left, str) and isinstance(right, str):
        return _ordinal_sign(
            left.casefold() if fold else left,
            right.casefold() if fold else right,
        )
    if isinstance(left, str) or isinstance(right, str):
        # One side text and the other not: M has no cross-type ordering, and
        # inventing one here would silently decide a comparison the language
        # itself refuses.
        raise UnsupportedError(
            f"{name}: cannot compare {_type_name(left)} with {_type_name(right)}"
        )
    return _non_text_compare(name, left, right)


def _comparer_ordinal(args: list[Any], ctx: _Ctx) -> Any:
    # Comparer.Ordinal(x as any, y as any) as number. Python's `<`/`>` on
    # `str` compares by codepoint, which is exactly Ordinal semantics.
    _arity("Comparer.Ordinal", args, 2)
    return _compare_any("Comparer.Ordinal", args[0], args[1], fold=False)


_comparer_ordinal.m_is_builtin_comparer = True  # type: ignore[attr-defined]


def _comparer_ordinal_ignore_case(args: list[Any], ctx: _Ctx) -> Any:
    # Case-insensitive Ordinal comparison (verified:
    # Comparer.OrdinalIgnoreCase("Abc", "abc") -> 0). `casefold()` is
    # Python's own recommended normalisation specifically for caseless
    # comparison - applying Ordinal's codepoint rule to the case-folded
    # text is the literal reading of "Ordinal, ignoring case", not a guess
    # at a different algorithm.
    _arity("Comparer.OrdinalIgnoreCase", args, 2)
    return _compare_any("Comparer.OrdinalIgnoreCase", args[0], args[1], fold=True)


_comparer_ordinal_ignore_case.m_is_builtin_comparer = True  # type: ignore[attr-defined]


# Shared between _comparer_from_culture (reached if it is actually called,
# e.g. `Comparer.FromCulture("en-US")`) and _resolve_text_comparer (reached
# if the bare, uninvoked identifier is passed as a `comparer` argument
# instead) so the two call paths give one identical message rather than
# two that could drift apart.
_FROM_CULTURE_MESSAGE = (
    "Comparer.FromCulture: culture-aware comparison is not implemented "
    "(no locale/collation data is available here - use Comparer.Ordinal or "
    "Comparer.OrdinalIgnoreCase instead)"
)


def _comparer_from_culture(args: list[Any], ctx: _Ctx) -> Any:
    # Comparer.FromCulture(culture as text, optional ignoreCase as
    # nullable logical) as function - real PQ returns a comparer curried
    # over `culture`. Collation tables are locale data this evaluator does
    # not ship, and guessing at one culture's sort/fold order would be
    # exactly the wrong-answer-shaped-right failure this package refuses
    # to make, so this refuses unconditionally instead of half-implementing
    # it. Still registered (rather than left as an unknown identifier) so
    # the refusal names itself precisely instead of "unknown identifier".
    _arity("Comparer.FromCulture", args, 1, 2)
    raise UnsupportedError(_FROM_CULTURE_MESSAGE)


_comparer_from_culture.m_is_builtin_comparer = True  # type: ignore[attr-defined]


def _comparer_equals(args: list[Any], ctx: _Ctx) -> Any:
    # Comparer.Equals(comparer as function, x as any, y as any) as logical.
    # "Returns a logical value based on the equality check over the two
    # given values ... using the provided comparer" - a comparer is
    # "a function that accepts two arguments and returns -1, 0, or 1", so
    # this invokes it and reads whether the result is exactly 0. No
    # upfront "is `comparer` a function" check: ctx.invoke already raises
    # `"<type> value is not a function"` for a non-invocable value, the
    # same way every OTHER comparer-accepting function in this codebase
    # (List.Sort's criteria, List.PositionOf's equationCriteria, ...)
    # already lets that error come from the call itself rather than
    # duplicating the check.
    #
    # The page's only worked example - Comparer.Equals(Comparer.
    # FromCulture("en-US"), "1", "A") -> false - never actually reaches
    # this function: `Comparer.FromCulture("en-US")` is itself a call, and
    # M evaluates call arguments eagerly, so `_comparer_from_culture`
    # above raises UnsupportedError before Comparer.Equals is ever
    # invoked. Pinned instead against Comparer.Ordinal/
    # Comparer.OrdinalIgnoreCase, the two comparers this package actually
    # implements - see test_tail_namespaces.py.
    _arity("Comparer.Equals", args, 3)
    comparer, x, y = args
    result = ctx.invoke(comparer, [x, y], ctx)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise EvalError(
            f"Comparer.Equals: comparer must return a number, got {_type_name(result)}"
        )
    return result == 0


def _resolve_text_comparer(value: Any, fn_name: str) -> bool:
    """True if the optional `comparer` argument requests case-insensitive
    matching; False for exact/no comparer.

    Only Comparer.Ordinal (exact - the default, so a no-op here) and
    Comparer.OrdinalIgnoreCase (case-insensitive) are implemented. A call
    expression like `Comparer.FromCulture("en-US")` never reaches this
    function at all - M evaluates call arguments eagerly, so
    `_comparer_from_culture` above has already raised before `comparer`
    is bound here. Only the bare, uninvoked `Comparer.FromCulture`
    identifier reaches this branch. Any other function value is refused
    outright - guessing at an unrecognised comparer's semantics is the
    wrong-answer-shaped-right failure this package refuses to make.
    """
    if value is _comparer_ordinal:
        return False
    if value is _comparer_ordinal_ignore_case:
        return True
    if value is _comparer_from_culture:
        raise UnsupportedError(_FROM_CULTURE_MESSAGE)
    raise UnsupportedError(
        f"{fn_name}: comparer must be Comparer.Ordinal or Comparer.OrdinalIgnoreCase"
    )


def _text_find_all(text: str, substring: str, ignore_case: bool) -> list[int]:
    """All non-overlapping match positions of `substring` in `text`, left
    to right - mirrors the case-sensitive path's original `str.find`-loop
    stepping exactly (a zero-length substring still advances by at least
    one position each step), verified equivalent for `ignore_case=False`
    since `re.escape` + a literal pattern searches identically to
    `str.find`.
    """
    pattern = re.compile(re.escape(substring), re.IGNORECASE if ignore_case else 0)
    positions: list[int] = []
    start = 0
    while True:
        match = pattern.search(text, start)
        if match is None:
            break
        positions.append(match.start())
        start = match.start() + max(len(substring), 1)
    return positions


def _find_last_ci(text: str, substring: str) -> int:
    """Case-insensitive equivalent of `str.rfind` - the rightmost start
    position, OVERLAPPING matches allowed. Deliberately not built on
    `_text_find_all`'s non-overlapping stepping: `str.rfind` finds the true
    rightmost occurrence even where it overlaps an earlier one (e.g.
    `"aaaaa".rfind("aa")` -> `3`, not `2`), so an occurrence-preserving
    case-insensitive version has to allow that too, by scanning candidate
    start positions from the end rather than reusing the left-to-right
    non-overlapping scan used for Occurrence.All.
    """
    if not substring:
        return len(text)
    folded = substring.casefold()
    for start in range(len(text) - len(substring), -1, -1):
        if text[start : start + len(substring)].casefold() == folded:
            return start
    return -1


def _text_starts_with(args: list[Any], ctx: _Ctx) -> Any:
    # as nullable logical - text is nullable and null propagates.
    _arity("Text.StartsWith", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    substring = _require_str(args[1])
    if len(args) == 3 and args[2] is not None:
        if _resolve_text_comparer(args[2], "Text.StartsWith"):
            return re.match(re.escape(substring), text, re.IGNORECASE) is not None
    return text.startswith(substring)


def _text_ends_with(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.EndsWith", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    substring = _require_str(args[1])
    if len(args) == 3 and args[2] is not None:
        if _resolve_text_comparer(args[2], "Text.EndsWith"):
            return (
                re.search(re.escape(substring) + r"\Z", text, re.IGNORECASE) is not None
            )
    return text.endswith(substring)


# Occurrence.First / Occurrence.Last / Occurrence.All are the real M enum
# values (0 / 1 / 2 respectively - confirmed against Occurrence.Type docs).
# Like Order.Ascending/Descending (see _table.py), the bare identifiers
# (enum resolution now lives in _enums.py - see its docstring)
# import, which is out of this module's ownership (evaluate.py and
# _table.py are off-limits for this task). Passing the literal number
# (0/1/2) works today; a caller writing the bare `Occurrence.Last`
# identifier gets evaluate.py's generic "unknown identifier" error until
# whichever module owns that wiring adds an Occurrence enum the same way
# (enum resolution now lives in _enums.py - see its docstring)
_OCCURRENCE_FIRST = 0
_OCCURRENCE_LAST = 1
_OCCURRENCE_ALL = 2


def _text_position_of(args: list[Any], ctx: _Ctx) -> Any:
    # Text.PositionOf(text as text, substring as text, optional occurrence
    # as nullable number, optional comparer as nullable function) as any.
    # Returns -1 when not found (verified). text/substring are non-nullable
    # in the real signature, so no null propagation here.
    _arity("Text.PositionOf", args, 2, 4)
    text = _require_str(args[0])
    substring = _require_str(args[1])
    occurrence = _OCCURRENCE_FIRST
    if len(args) >= 3 and args[2] is not None:
        occurrence = _require_int(args[2])
        if occurrence not in (_OCCURRENCE_FIRST, _OCCURRENCE_LAST, _OCCURRENCE_ALL):
            raise UnsupportedError(
                "Text.PositionOf: occurrence must be Occurrence.First (0), "
                "Occurrence.Last (1), or Occurrence.All (2)"
            )
    ignore_case = False
    if len(args) == 4 and args[3] is not None:
        ignore_case = _resolve_text_comparer(args[3], "Text.PositionOf")
    if not ignore_case:
        # Unchanged from before comparer support - str.find/str.rfind
        # exactly, so the no-comparer call shape is byte-identical to
        # what it was.
        if occurrence == _OCCURRENCE_FIRST:
            return text.find(substring)
        if occurrence == _OCCURRENCE_LAST:
            return text.rfind(substring)
        positions: list[int] = []
        start = 0
        while True:
            pos = text.find(substring, start)
            if pos == -1:
                break
            positions.append(pos)
            start = pos + max(len(substring), 1)
        return positions
    if occurrence == _OCCURRENCE_LAST:
        return _find_last_ci(text, substring)
    ci_positions = _text_find_all(text, substring, ignore_case=True)
    if occurrence == _OCCURRENCE_ALL:
        return ci_positions
    return ci_positions[0] if ci_positions else -1


def _text_position_of_any(args: list[Any], ctx: _Ctx) -> Any:
    # Text.PositionOfAny(text as text, characters as list, optional
    # occurrence as nullable number) as any. `characters` is a list of
    # single characters (verified via MS docs example); returns the first
    # position where ANY of them occurs, -1 if none do (Occurrence.All ->
    # list of all such positions, [] if none).
    _arity("Text.PositionOfAny", args, 2, 3)
    text = _require_str(args[0])
    chars = _char_set(args[1], "Text.PositionOfAny")
    occurrence = _OCCURRENCE_FIRST
    if len(args) == 3 and args[2] is not None:
        occurrence = _require_int(args[2])
        if occurrence not in (_OCCURRENCE_FIRST, _OCCURRENCE_LAST, _OCCURRENCE_ALL):
            raise UnsupportedError(
                "Text.PositionOfAny: occurrence must be Occurrence.First (0), "
                "Occurrence.Last (1), or Occurrence.All (2)"
            )
    positions = [i for i, c in enumerate(text) if c in chars]
    if occurrence == _OCCURRENCE_ALL:
        return positions
    if not positions:
        return -1
    return positions[0] if occurrence == _OCCURRENCE_FIRST else positions[-1]


def _text_insert(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Insert", args, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    offset = _require_int(args[1])
    if offset < 0 or offset > len(text):
        raise EvalError("Text.Insert: offset out of range")
    new_text = _require_str(args[2])
    return text[:offset] + new_text + text[offset:]


def _text_proper(args: list[Any], ctx: _Ctx) -> Any:
    # Text.Proper(text as nullable text, optional culture as nullable text)
    # as nullable text - capitalizes the first letter of each word,
    # lowercases the rest. Word boundary = any non-letter character
    # (verified exactly against the MS docs worked example). Only the
    # documented space-separated case is pinned; behaviour on apostrophes
    # inside a word is not documented and is not asserted on.
    _arity("Text.Proper", args, 1, 2)
    if len(args) == 2 and args[1] is not None:
        raise UnsupportedError("Text.Proper: culture argument")
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    result: list[str] = []
    capitalize_next = True
    for ch in text:
        if ch.isalpha():
            result.append(ch.upper() if capitalize_next else ch.lower())
            capitalize_next = False
        else:
            result.append(ch)
            capitalize_next = True
    return "".join(result)


def _text_clean(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Clean", args, 1)
    text = args[0]
    if text is None:
        return None
    return "".join(c for c in _require_str(text) if ord(c) >= 32)


def _text_trim_start(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.TrimStart", args, 1, 2)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    if len(args) == 2 and args[1] is not None:
        return text.lstrip(_require_str(args[1]))
    return text.lstrip()


def _text_trim_end(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.TrimEnd", args, 1, 2)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    if len(args) == 2 and args[1] is not None:
        return text.rstrip(_require_str(args[1]))
    return text.rstrip()


def _text_to_list(args: list[Any], ctx: _Ctx) -> Any:
    # Text.ToList(text as text) as list - not nullable in the real
    # signature, so no null propagation (matches _require_str raising).
    _arity("Text.ToList", args, 1)
    return list(_require_str(args[0]))


def _text_at(args: list[Any], ctx: _Ctx) -> Any:
    # Text.At(text as nullable text, index as number) as nullable text.
    # The nullable return type (and no documented error case) means an
    # out-of-range index returns null rather than throwing.
    _arity("Text.At", args, 2)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    index = _require_int(args[1])
    if index < 0 or index >= len(text):
        return None
    return text[index]


def _text_split_any(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.SplitAny", args, 2)
    text = _require_str(args[0])
    separators = _require_str(args[1])
    if not separators:
        return [text]
    result: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch in separators:
            result.append("".join(current))
            current = []
        else:
            current.append(ch)
    result.append("".join(current))
    return result


def _text_new_guid(args: list[Any], ctx: _Ctx) -> Any:
    # Text.NewGuid() as text - non-deterministic by definition; callers
    # must not assert on its exact value, only its shape.
    _arity("Text.NewGuid", args, 0)
    return str(_uuid.uuid4()).upper()


def _text_range(args: list[Any], ctx: _Ctx) -> Any:
    # Text.Range(text as nullable text, offset as number, optional count as
    # nullable number) as nullable text. Verified against the MS docs' own
    # two worked examples: Text.Range("Hello World", 6) -> "World" and
    # Text.Range("Hello World Hello", 6, 5) -> "World". Trap (verified from
    # the docs' own words, "Raises an error if there aren't enough
    # characters"): unlike Text.Middle, which silently CLAMPS an over-long
    # start/count, Range throws instead - the two functions are not
    # interchangeable at the boundary even though both slice text.
    _arity("Text.Range", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    offset = _require_int(args[1])
    if offset < 0:
        raise EvalError("Text.Range: offset must not be negative")
    if offset > len(text):
        raise EvalError("Text.Range: not enough characters")
    if len(args) == 3 and args[2] is not None:
        count = _require_int(args[2])
        if count < 0:
            raise EvalError("Text.Range: count must not be negative")
        if offset + count > len(text):
            raise EvalError("Text.Range: not enough characters")
        return text[offset : offset + count]
    return text[offset:]


def _text_remove_range(args: list[Any], ctx: _Ctx) -> Any:
    # Text.RemoveRange(text as nullable text, offset as number, optional
    # count as nullable number) as nullable text. `count` defaults to 1
    # (verified: the docs' example 1 omits count and removes exactly one
    # character: Text.RemoveRange("ABCDE", 2) -> "ABDE"). Example 2:
    # Text.RemoveRange("ABCDE", 1, 2) -> "ADE".
    #
    # This page does not give a worked example for an out-of-range
    # offset/count, so raising here is a CHOICE, not a docs-verified fact
    # for this specific function - it follows sibling Text.Range (which
    # explicitly documents "raises an error if there aren't enough
    # characters" for the identical offset+count shape) and .NET's own
    # String.Remove(startIndex, count), which throws on the same condition,
    # rather than silently clamping.
    _arity("Text.RemoveRange", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    offset = _require_int(args[1])
    if offset < 0:
        raise EvalError("Text.RemoveRange: offset must not be negative")
    if offset > len(text):
        raise EvalError("Text.RemoveRange: not enough characters")
    count = 1
    if len(args) == 3 and args[2] is not None:
        count = _require_int(args[2])
        if count < 0:
            raise EvalError("Text.RemoveRange: count must not be negative")
    if offset + count > len(text):
        raise EvalError("Text.RemoveRange: not enough characters")
    return text[:offset] + text[offset + count :]


def _text_replace_range(args: list[Any], ctx: _Ctx) -> Any:
    # Text.ReplaceRange(text as nullable text, offset as number, count as
    # number, newText as text) as nullable text - `count` is NOT optional
    # here (unlike RemoveRange). Verified against the docs' own worked
    # example: Text.ReplaceRange("ABGF", 2, 1, "CDE") -> "ABCDEF" (the "G"
    # at offset 2 is removed and "CDE" is inserted in its place). Same
    # out-of-range-raises choice as Text.RemoveRange above, for the same
    # reason.
    _arity("Text.ReplaceRange", args, 4)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    offset = _require_int(args[1])
    if offset < 0:
        raise EvalError("Text.ReplaceRange: offset must not be negative")
    if offset > len(text):
        raise EvalError("Text.ReplaceRange: not enough characters")
    count = _require_int(args[2])
    if count < 0:
        raise EvalError("Text.ReplaceRange: count must not be negative")
    if offset + count > len(text):
        raise EvalError("Text.ReplaceRange: not enough characters")
    new_text = _require_str(args[3])
    return text[:offset] + new_text + text[offset + count :]


# TextEncoding.* enum identifiers (TextEncoding.Utf8, TextEncoding.Utf16,
# ...) are deliberately NOT registered anywhere in this evaluator - see
# _enums.py's own docstring: their numbering was never confirmed, so a bare
# `TextEncoding.Utf8` identifier already fails with "unknown identifier"
# before Text.ToBinary is reached. The reachable call shape is therefore the
# numeric Windows code page directly - the same shape Text.FromBinary's own
# `encoding` argument already takes (see _connectors.py). Only the three
# encodings with a UNAMBIGUOUS, standard byte-order-mark are supported for
# `includeByteOrderMark`; the rest refuse rather than guess a BOM that does
# not exist for that encoding.
_BOM_BYTES: dict[int, bytes] = {
    65001: b"\xef\xbb\xbf",  # UTF-8
    1200: b"\xff\xfe",  # UTF-16 LE ("Unicode" / TextEncoding.Utf16)
    1201: b"\xfe\xff",  # UTF-16 BE ("BigEndianUnicode")
}


def _require_bool(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise EvalError(f"{what}: expected a logical value, got {_type_name(value)}")
    return value


def _text_to_binary(args: list[Any], ctx: _Ctx) -> Any:
    # Text.ToBinary(text as nullable text, optional encoding as nullable
    # number, optional includeByteOrderMark as nullable logical) as
    # nullable binary. Verified against the MS docs' own two worked
    # examples: default UTF-8 encoding of "Testing 1-2-3" base64-encodes to
    # exactly "VGVzdGluZyAxLTItMw==" (cross-checked with Python's own
    # base64.b64encode), and TextEncoding.Utf16 (code page 1200, which
    # `_CODE_PAGES` already maps to "utf-16-le") with
    # includeByteOrderMark=true produces the exact hex
    # "fffe540065007300740069006e006700200031002d0032002d003300" (cross-
    # checked byte-for-byte). Python's explicit "utf-16-le"/"utf-16-be"
    # codecs never emit a BOM on `.encode()` (only the generic "utf-16"
    # codec does, and only in native/ambiguous endianness), so the BOM is
    # prepended by hand here rather than relying on the codec to add one.
    _arity("Text.ToBinary", args, 1, 3)
    text = args[0]
    if text is None:
        return None
    text = _require_str(text)
    code_page = (
        _DEFAULT_ENCODING if len(args) < 2 or args[1] is None else _require_int(args[1])
    )
    codec = _CODE_PAGES.get(code_page)
    if codec is None:
        raise UnsupportedError(
            f"Text.ToBinary: text encoding code page {code_page} (known: "
            + ", ".join(str(k) for k in sorted(_CODE_PAGES))
            + ")"
        )
    include_bom = False
    if len(args) == 3 and args[2] is not None:
        include_bom = _require_bool(args[2], "Text.ToBinary")
    try:
        encoded = text.encode(codec)
    except UnicodeEncodeError as error:
        raise EvalError(f"Text.ToBinary: {error}") from error
    if not include_bom:
        return encoded
    bom = _BOM_BYTES.get(code_page)
    if bom is None:
        raise UnsupportedError(
            f"Text.ToBinary: includeByteOrderMark for code page {code_page} "
            "(no standard byte-order mark is defined for this encoding)"
        )
    return bom + encoded


def _text_infer_number_type(args: list[Any], ctx: _Ctx) -> Any:
    # Text.InferNumberType(text as text, optional culture as nullable text)
    # as type. The MS docs page for this function has NO worked example -
    # it says only "Infers the granular number type (Int64.Type,
    # Double.Type, and so on)". It does not say which text forms select
    # which of the ~9 granular number subtypes (Int64 vs Double vs Decimal
    # vs Currency vs Percentage), what the Int64-range overflow boundary
    # does, or how the optional culture argument changes parsing. Guessing
    # at that mapping is exactly the plausible-wrong-answer failure this
    # package refuses to produce - the same reasoning _type.py's own
    # docstring already gives for refusing Value.Type/Value.Is on values it
    # cannot classify - so this refuses by name instead of picking a
    # subtype. Still registered, per this codebase's own convention (see
    # Comparer.FromCulture above), so the refusal names itself precisely
    # rather than surfacing as "unknown identifier".
    _arity("Text.InferNumberType", args, 1, 2)
    _check_invariant_culture(
        "Text.InferNumberType",
        args[1] if len(args) == 2 else None,
        "culture-specific number-type inference",
    )
    raise UnsupportedError(
        "Text.InferNumberType: granular number-subtype selection (Int64.Type "
        "vs Double.Type vs Decimal.Type vs Currency.Type vs Percentage.Type) "
        "has no worked example in the M docs to pin it against, so this "
        "module does not guess which subtype a given text maps to"
    )


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
BUILTINS: dict[str, Any] = {
    "Text.From": _text_from,
    "Text.Format": _text_format,
    "Text.Upper": _text_upper,
    "Text.Lower": _text_lower,
    "Text.Length": _text_length,
    "Text.Combine": _text_combine,
    "Text.Contains": _text_contains,
    "Text.Replace": _text_replace,
    "Text.Split": _text_split,
    "Text.Start": _text_start,
    "Text.End": _text_end,
    "Text.Trim": _text_trim,
    "Text.PadStart": _text_pad_start,
    "Text.PadEnd": _text_pad_end,
    "Text.Middle": _text_middle,
    "Text.BeforeDelimiter": _text_before_delimiter,
    "Text.AfterDelimiter": _text_after_delimiter,
    "Text.BetweenDelimiters": _text_between_delimiters,
    "Text.Select": _text_select,
    "Text.Remove": _text_remove,
    "Text.Repeat": _text_repeat,
    "Text.Reverse": _text_reverse,
    "Text.StartsWith": _text_starts_with,
    "Text.EndsWith": _text_ends_with,
    "Text.PositionOf": _text_position_of,
    "Text.PositionOfAny": _text_position_of_any,
    "Comparer.Ordinal": _comparer_ordinal,
    "Comparer.OrdinalIgnoreCase": _comparer_ordinal_ignore_case,
    "Comparer.FromCulture": _comparer_from_culture,
    "Comparer.Equals": _comparer_equals,
    "Text.Insert": _text_insert,
    "Text.Proper": _text_proper,
    "Text.Clean": _text_clean,
    "Text.TrimStart": _text_trim_start,
    "Text.TrimEnd": _text_trim_end,
    "Text.ToList": _text_to_list,
    "Text.At": _text_at,
    "Text.SplitAny": _text_split_any,
    "Text.NewGuid": _text_new_guid,
    "Text.Range": _text_range,
    "Text.RemoveRange": _text_remove_range,
    "Text.ReplaceRange": _text_replace_range,
    "Text.ToBinary": _text_to_binary,
    "Text.InferNumberType": _text_infer_number_type,
}
