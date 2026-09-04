"""``Text.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
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


def _text_starts_with(args: list[Any], ctx: _Ctx) -> Any:
    # as nullable logical - text is nullable and null propagates.
    _arity("Text.StartsWith", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("Text.StartsWith: comparer argument")
    return _require_str(text).startswith(_require_str(args[1]))


def _text_ends_with(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.EndsWith", args, 2, 3)
    text = args[0]
    if text is None:
        return None
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("Text.EndsWith: comparer argument")
    return _require_str(text).endswith(_require_str(args[1]))


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
    if len(args) == 4 and args[3] is not None:
        raise UnsupportedError("Text.PositionOf: comparer argument")
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


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
BUILTINS: dict[str, Any] = {
    "Text.From": _text_from,
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
    "Text.Insert": _text_insert,
    "Text.Proper": _text_proper,
    "Text.Clean": _text_clean,
    "Text.TrimStart": _text_trim_start,
    "Text.TrimEnd": _text_trim_end,
    "Text.ToList": _text_to_list,
    "Text.At": _text_at,
    "Text.SplitAny": _text_split_any,
    "Text.NewGuid": _text_new_guid,
}
