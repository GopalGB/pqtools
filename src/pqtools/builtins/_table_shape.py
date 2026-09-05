"""Reshaping ``Table.*`` builtins (Unpivot/Pivot/Transpose/SplitColumn/...)
plus the everyday ``Table.*`` verbs that appear in nearly every real query
(Skip/Range/ReorderColumns/Combine/...) - see PRD-0.5.0-builtins.md.

Owned by exactly one implementer. Add functions here and register them in
this module's own ``BUILTINS`` dict; ``builtins/__init__.py`` merges every
module's dict, so there is no central registry file to edit and no
cross-family merge conflict.

Two design notes that apply to most functions below:

- **Column order.** A table is a ``list[dict[str, Any]]`` (see
  ``evaluate.py``'s module docstring); a row's dict key order *is* its
  column order, and (per the established convention already in
  ``_table.py``'s ``Table.ColumnNames``) an empty table has no known
  columns - that is the honest consequence of a data model with no schema
  independent of its rows, not a guess.
- **Enum-like bare identifiers.** Power Query's UI emits arguments such as
  ``QuoteStyle.Csv`` or ``ExtraValues.List`` as plain identifiers, not
  string literals. ``evaluate.py``'s identifier resolution
  (``_eval_identifier_expression``) resolves any unbound name by looking it up
  in ``BUILTINS``, so the extension point is simply: register the constant in
  this module's own ``BUILTINS`` dict, keyed by its qualified name, holding
  a plain sentinel value nothing else in the system would produce by
  accident. See the bottom of this file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ._list import _equation_criteria_predicate
from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _field_name_list,
    _format_number,
    _m_equal,
    _require_int,
    _require_list,
    _require_number,
    _require_record,
    _require_str,
    _require_table,
    _sort_criteria,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def _is_invocable(value: Any) -> bool:
    """True if ``ctx.invoke(value, ...)`` can call ``value`` as an M function.

    A BUILTINS-registered function is a plain Python callable. An ``each
    .../(...) => ...`` closure is a ``_Lambda`` from ``evaluate.py`` -
    deliberately not importable here (the same circular-import chain
    ``_Ctx.invoke``'s docstring in ``evaluate.py`` explains: ``evaluate.py``
    imports the ``BUILTINS`` registry from this package, so this package
    cannot import back from ``evaluate.py`` at runtime). ``_Lambda`` has no
    ``__call__``, so ``callable()`` alone would miss it; duck-type on its
    ``__slots__`` (``params``, ``body``, ``scope``) instead.
    """
    return callable(value) or (
        hasattr(value, "params") and hasattr(value, "body") and hasattr(value, "scope")
    )


def _column_order(table: list[dict[str, Any]]) -> list[str]:
    return list(table[0].keys()) if table else []


def _to_column_name(value: Any, what: str) -> str:
    """Text.From-equivalent, used to turn a pivot value into a column name."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return _format_number(value)
    raise EvalError(f"{what}: cannot use a {_type_name(value)} value as a column name")


def _parse_comparison_keys(spec: Any, what: str) -> list[tuple[str, bool]]:
    """Kept as a name so call sites read the same; the logic lives in _shared.

    It used to be a second copy of `_table.py`'s parser "to keep this module
    self-contained". Both copies then rejected Microsoft's own documented
    `{"Col", Order.Descending}` bare pair, which is what two copies of a rule
    buys you.
    """
    return _sort_criteria(spec, what)


def _distribute_pieces(
    pieces: list[Any] | None,
    column_names: list[str],
    default: Any,
    extra: str,
    what: str,
) -> dict[str, Any]:
    """Map a splitter's output onto ``column_names`` slots.

    Shared by ``Table.SplitColumn`` and ``Table.FromList``, which both build
    a row this same way: ``default`` fills a slot with no corresponding
    piece; ``extra`` (an ``ExtraValues.*`` sentinel) controls what happens
    to the LAST slot when there are more pieces than slots -
    ``ExtraValues.Ignore`` (default) keeps only ``pieces[slot_count - 1]``,
    ``ExtraValues.Error`` raises, and ``ExtraValues.List`` puts
    ``pieces[slot_count - 1:]`` (a list) in the last slot - but ONLY when a
    piece actually reaches that slot; if the row runs out of pieces before
    the last slot, the last slot gets ``default`` like any other missing
    slot, not an empty list. (Reasoned from Power Query's documented
    ``Table.SplitColumn`` "Cristina J. Best"/"Bob White"/"Paul" example,
    where the always-a-list behaviour only applies to slots pieces reach;
    pinned by a test - this exact corner is not written out in the docs.)
    """
    slot_count = len(column_names)
    row: dict[str, Any] = {}
    if pieces is None:
        for name in column_names:
            row[name] = None
        return row
    if len(pieces) > slot_count and extra == "ExtraValues.Error":
        raise EvalError(f"{what}: more split values than columns")
    for i, name in enumerate(column_names):
        if i == slot_count - 1 and extra == "ExtraValues.List":
            row[name] = (
                pieces[slot_count - 1 :] if len(pieces) >= slot_count else default
            )
        elif i < len(pieces):
            row[name] = pieces[i]
        else:
            row[name] = default
    return row


def _find_unquoted(text: str, delimiter: str, quote_style: str) -> int:
    """Index of the first ``delimiter`` in ``text`` outside double quotes.

    ``QuoteStyle.Csv`` treats ``""`` inside a quoted region as an escaped
    quote (RFC4180-style), matching a delimiter only outside such regions.
    ``QuoteStyle.None`` (or no quotes present) is a plain ``str.find``.
    Returns -1 if not found.
    """
    if quote_style != "QuoteStyle.Csv" or '"' not in text:
        return text.find(delimiter)
    in_quotes = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            if in_quotes and i + 1 < n and text[i + 1] == '"':
                i += 2
                continue
            in_quotes = not in_quotes
            i += 1
            continue
        if not in_quotes and text.startswith(delimiter, i):
            return i
        i += 1
    return -1


def _unquote_csv_field(field: str) -> str:
    if len(field) >= 2 and field[0] == '"' and field[-1] == '"':
        return field[1:-1].replace('""', '"')
    return field


def _split_delimiter_csv_style(text: str, delimiter: str) -> list[Any]:
    pieces: list[Any] = []
    remaining = text
    while True:
        index = _find_unquoted(remaining, delimiter, "QuoteStyle.Csv")
        if index == -1:
            pieces.append(_unquote_csv_field(remaining))
            return pieces
        pieces.append(_unquote_csv_field(remaining[:index]))
        remaining = remaining[index + len(delimiter) :]


# --------------------------------------------------------------------------
# Reshaping - Unpivot / Pivot / Transpose
# --------------------------------------------------------------------------


def _unpivot_core(
    table: list[dict[str, Any]],
    columns: list[str],
    attribute_column: str,
    value_column: str,
    what: str,
) -> Any:
    header = _column_order(table)
    for name in columns:
        if table and name not in header:
            raise EvalError(f"{what}: no such column: {name}")
    kept = [c for c in header if c not in columns]
    if attribute_column in kept or value_column in kept:
        raise EvalError(
            f"{what}: attributeColumn/valueColumn name clashes with a kept column"
        )
    result: list[dict[str, Any]] = []
    for row in table:
        base = {name: row[name] for name in kept}
        for name in columns:
            value = row[name]
            # Power Query's own docs example (Table.Unpivot) drops a cell
            # from the output when its value is null - verified against
            # learn.microsoft.com/en-us/powerquery-m/table-unpivot's worked
            # example (b=null and c=null cells are absent from the output,
            # while a zero/empty-string value would not be). Only null is
            # dropped here, nothing else.
            if value is None:
                continue
            new_row = dict(base)
            new_row[attribute_column] = name
            new_row[value_column] = value
            result.append(new_row)
    return result


def _table_unpivot(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Unpivot", args, 4)
    table = _require_table(args[0])
    columns = _field_name_list(args[1])
    attribute_column = _require_str(args[2])
    value_column = _require_str(args[3])
    return _unpivot_core(
        table, columns, attribute_column, value_column, "Table.Unpivot"
    )


def _table_unpivot_other_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.UnpivotOtherColumns", args, 4)
    table = _require_table(args[0])
    not_unpivoted = _field_name_list(args[1])
    attribute_column = _require_str(args[2])
    value_column = _require_str(args[3])
    header = _column_order(table)
    for name in not_unpivoted:
        if table and name not in header:
            raise EvalError(f"Table.UnpivotOtherColumns: no such column: {name}")
    melt_columns = [c for c in header if c not in not_unpivoted]
    return _unpivot_core(
        table, melt_columns, attribute_column, value_column, "Table.UnpivotOtherColumns"
    )


def _table_pivot(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Pivot", args, 4, 5)
    table = _require_table(args[0])
    pivot_values = _require_list(args[1])
    attribute_column = _require_str(args[2])
    value_column = _require_str(args[3])
    agg_fn = args[4] if len(args) == 5 else None
    header = _column_order(table)
    if table and attribute_column not in header:
        raise EvalError(f"Table.Pivot: no such column: {attribute_column}")
    if table and value_column not in header:
        raise EvalError(f"Table.Pivot: no such column: {value_column}")
    key_columns = [c for c in header if c not in (attribute_column, value_column)]

    # Group by the non-attribute/value columns, preserving first-occurrence
    # order (mirrors the real M standard-library implementation, which
    # builds Table.Pivot on top of Table.Group over exactly these columns).
    # Grouping uses a linear scan + `==` rather than a dict, matching
    # `_table_distinct`'s precedent elsewhere in this codebase - list/record
    # column values are not hashable, but `==` works on them directly.
    group_keys: list[tuple[Any, ...]] = []
    group_rows: list[list[dict[str, Any]]] = []
    for row in table:
        key = tuple(row[c] for c in key_columns)
        try:
            idx = group_keys.index(key)
        except ValueError:
            group_keys.append(key)
            group_rows.append([row])
        else:
            group_rows[idx].append(row)

    result: list[dict[str, Any]] = []
    for key, rows in zip(group_keys, group_rows, strict=True):
        out_row: dict[str, Any] = dict(zip(key_columns, key, strict=True))
        for pivot_value in pivot_values:
            column_name = _to_column_name(pivot_value, "Table.Pivot")
            if column_name in out_row:
                raise EvalError(
                    f"Table.Pivot: duplicate resulting column name: {column_name}"
                )
            matches = [
                r[value_column]
                for r in rows
                if _m_equal(r[attribute_column], pivot_value)
            ]
            if agg_fn is not None:
                out_row[column_name] = ctx.invoke(agg_fn, [matches], ctx)
            elif not matches:
                out_row[column_name] = None
            elif len(matches) == 1:
                out_row[column_name] = matches[0]
            else:
                # Real Power Query errors here unless an aggregation
                # function is supplied - it never silently picks one value
                # out of several (PRD-0.5.0-builtins.md "Correctness
                # rules"). Pinned by a test.
                raise EvalError(
                    f"Table.Pivot: multiple values for pivoted column "
                    f"{column_name!r} - supply an aggregation function"
                )
        result.append(out_row)
    return result


def _table_transpose(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Transpose", args, 1, 2)
    if len(args) == 2 and args[1] is not None:
        raise UnsupportedError("Table.Transpose: columns option")
    table = _require_table(args[0])
    if not table:
        # This data model has no schema independent of its rows (see the
        # module docstring), so a 0-row table carries no column count for
        # Transpose to turn into rows - the necessary consequence of that
        # model, not a guess.
        return []
    header = _column_order(table)
    for row in table:
        if list(row.keys()) != header:
            raise EvalError("Table.Transpose: all rows must share the same columns")
    # Real Power Query's Table.Transpose discards the original column
    # NAMES entirely - only the data grid is transposed, and the result is
    # named Column1..ColumnN (verified against learn.microsoft.com's worked
    # example: a 3-row/2-column input becomes a 2-row/3-column output named
    # Column1/Column2/Column3, with the original "Name"/"Value" headers
    # nowhere in the output).
    new_column_names = [f"Column{i + 1}" for i in range(len(table))]
    result: list[dict[str, Any]] = []
    for column_name in header:
        result.append(
            {new_column_names[i]: table[i][column_name] for i in range(len(table))}
        )
    return result


# --------------------------------------------------------------------------
# FillDown / FillUp / AddIndexColumn
# --------------------------------------------------------------------------


def _fill(
    table: list[dict[str, Any]], columns: list[str], what: str, reverse: bool
) -> Any:
    if table:
        for name in columns:
            if name not in table[0]:
                raise EvalError(f"{what}: no such column: {name}")
    rows = [dict(row) for row in table]
    order = range(len(rows) - 1, -1, -1) if reverse else range(len(rows))
    last: dict[str, Any] = {}
    for i in order:
        row = rows[i]
        for name in columns:
            if row[name] is None:
                if name in last:
                    row[name] = last[name]
            else:
                last[name] = row[name]
    return rows


def _table_fill_down(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FillDown", args, 2)
    table = _require_table(args[0])
    columns = _field_name_list(args[1])
    return _fill(table, columns, "Table.FillDown", reverse=False)


def _table_fill_up(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FillUp", args, 2)
    table = _require_table(args[0])
    columns = _field_name_list(args[1])
    return _fill(table, columns, "Table.FillUp", reverse=True)


def _table_add_index_column(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.AddIndexColumn", args, 2, 4)
    table = _require_table(args[0])
    name = _require_str(args[1])
    initial: int | float = _require_number(args[2]) if len(args) >= 3 else 0
    increment: int | float = _require_number(args[3]) if len(args) == 4 else 1
    if table and name in table[0]:
        raise EvalError(f"Table.AddIndexColumn: column already exists: {name}")
    result: list[dict[str, Any]] = []
    value = initial
    for row in table:
        new_row = dict(row)
        new_row[name] = value
        result.append(new_row)
        value = value + increment
    return result


# --------------------------------------------------------------------------
# Table.SplitColumn + Splitter.* + Table.ReplaceValue + Replacer.*
# --------------------------------------------------------------------------


def _table_split_column(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.SplitColumn", args, 3, 6)
    table = _require_table(args[0])
    source_column = _require_str(args[1])
    splitter = args[2]
    if not _is_invocable(splitter):
        raise EvalError("Table.SplitColumn: splitter must be a function")
    names_or_number = args[3] if len(args) >= 4 else None
    default = args[4] if len(args) >= 5 else None
    extra = args[5] if len(args) >= 6 and args[5] is not None else "ExtraValues.Ignore"
    if extra not in ("ExtraValues.Ignore", "ExtraValues.Error", "ExtraValues.List"):
        raise UnsupportedError(f"Table.SplitColumn: extraColumns {extra!r}")
    if table and source_column not in table[0]:
        raise EvalError(f"Table.SplitColumn: no such column: {source_column}")

    split_values: list[list[Any] | None] = []
    for row in table:
        value = row[source_column]
        if value is None:
            split_values.append(None)
            continue
        pieces = ctx.invoke(splitter, [value], ctx)
        if not isinstance(pieces, list):
            raise EvalError("Table.SplitColumn: splitter must return a list")
        split_values.append(pieces)

    column_names: list[str]
    if names_or_number is None:
        max_pieces = max((len(p) for p in split_values if p is not None), default=1)
        column_names = [f"{source_column}.{i + 1}" for i in range(max(max_pieces, 1))]
    elif isinstance(names_or_number, list):
        column_names = [_require_str(n) for n in names_or_number]
        if not column_names:
            raise EvalError(
                "Table.SplitColumn: columnNamesOrNumber list must not be empty"
            )
    elif isinstance(names_or_number, (int, float)) and not isinstance(
        names_or_number, bool
    ):
        count = _require_int(names_or_number)
        if count < 1:
            raise EvalError("Table.SplitColumn: column count must be at least 1")
        column_names = [f"{source_column}.{i + 1}" for i in range(count)]
    else:
        raise EvalError(
            "Table.SplitColumn: columnNamesOrNumber must be a number or a list of names"
        )

    header = _column_order(table)
    for name in column_names:
        if name != source_column and name in header:
            raise EvalError(f"Table.SplitColumn: column already exists: {name}")

    result: list[dict[str, Any]] = []
    for row, pieces in zip(table, split_values, strict=True):
        new_row: dict[str, Any] = {}
        for key in header:
            if key != source_column:
                new_row[key] = row[key]
            else:
                new_row.update(
                    _distribute_pieces(
                        pieces, column_names, default, extra, "Table.SplitColumn"
                    )
                )
        result.append(new_row)
    return result


def _splitter_split_text_by_delimiter(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Splitter.SplitTextByDelimiter", args, 1, 2)
    delimiter = _require_str(args[0])
    if delimiter == "":
        raise EvalError("Splitter.SplitTextByDelimiter: delimiter must not be empty")
    quote_style = (
        args[1] if len(args) == 2 and args[1] is not None else "QuoteStyle.None"
    )
    if quote_style not in ("QuoteStyle.None", "QuoteStyle.Csv"):
        raise UnsupportedError(
            f"Splitter.SplitTextByDelimiter: quoteStyle {quote_style!r}"
        )

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByDelimiter (applied)", inner_args, 1)
        text = _require_str(inner_args[0])
        if quote_style == "QuoteStyle.Csv":
            return _split_delimiter_csv_style(text, delimiter)
        return text.split(delimiter)

    return _split


def _splitter_split_text_by_each_delimiter(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Splitter.SplitTextByEachDelimiter", args, 1, 3)
    delimiters = [_require_str(d) for d in _require_list(args[0])]
    for d in delimiters:
        if d == "":
            raise EvalError(
                "Splitter.SplitTextByEachDelimiter: delimiters must not be empty"
            )
    quote_style = (
        args[1] if len(args) >= 2 and args[1] is not None else "QuoteStyle.None"
    )
    if quote_style not in ("QuoteStyle.None", "QuoteStyle.Csv"):
        raise UnsupportedError(
            f"Splitter.SplitTextByEachDelimiter: quoteStyle {quote_style!r}"
        )
    start_at_end = args[2] if len(args) == 3 else False
    if not isinstance(start_at_end, bool):
        raise EvalError("Splitter.SplitTextByEachDelimiter: startAtEnd must be logical")

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByEachDelimiter (applied)", inner_args, 1)
        text = _require_str(inner_args[0])
        if not delimiters:
            return [text]
        # startAtEnd is implemented by reversing the text (and each
        # delimiter), running the forward cascading-split below, then
        # reversing each resulting piece and the list order - verified
        # against Power Query's own docs example
        # (Splitter.SplitTextByEachDelimiter({",", ";"}, QuoteStyle.None,
        # true)('a,"b;c",d') -> {"a,""b", "c""", "d"}), which this mirroring
        # reproduces exactly.
        if start_at_end:
            source = text[::-1]
            delims = [d[::-1] for d in delimiters]
        else:
            source = text
            delims = delimiters
        pieces: list[str] = []
        remaining = source
        for delimiter in delims:
            index = _find_unquoted(remaining, delimiter, quote_style)
            if index == -1:
                pieces.append(remaining)
                remaining = ""
            else:
                pieces.append(remaining[:index])
                remaining = remaining[index + len(delimiter) :]
        pieces.append(remaining)
        if start_at_end:
            pieces = [p[::-1] for p in reversed(pieces)]
        return pieces

    return _split


def _splitter_split_text_by_positions(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Splitter.SplitTextByPositions", args, 1, 2)
    positions = [_require_int(p) for p in _require_list(args[0])]
    start_at_end = args[1] if len(args) == 2 else False
    if not isinstance(start_at_end, bool):
        raise EvalError("Splitter.SplitTextByPositions: startAtEnd must be logical")

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByPositions (applied)", inner_args, 1)
        text = _require_str(inner_args[0])
        source = text[::-1] if start_at_end else text
        if not positions:
            pieces = [source]
        else:
            pieces = [
                source[positions[i] : positions[i + 1]]
                for i in range(len(positions) - 1)
            ]
            pieces.append(source[positions[-1] :])
        if start_at_end:
            # Mirrors the docs example exactly: reverse the text, split,
            # reverse each piece, reverse the list order (see
            # Splitter.SplitTextByPositions({0, 5}, true)("Redmond98052")
            # -> {"Redmond", "98052"}).
            pieces = [p[::-1] for p in reversed(pieces)]
        return pieces

    return _split


def _splitter_split_text_by_character_transition(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Splitter.SplitTextByCharacterTransition", args, 2)
    before, after = args[0], args[1]

    def _in_class(ch: str, spec: Any, inner_ctx: _Ctx) -> bool:
        if isinstance(spec, list):
            return ch in spec
        result = inner_ctx.invoke(spec, [ch], inner_ctx)
        if not isinstance(result, bool):
            raise EvalError(
                "Splitter.SplitTextByCharacterTransition: character predicate "
                "must return a logical value"
            )
        return result

    def _split(inner_args: list[Any], inner_ctx: _Ctx) -> Any:
        _arity("Splitter.SplitTextByCharacterTransition (applied)", inner_args, 1)
        text = _require_str(inner_args[0])
        if not text:
            return [""]
        pieces = []
        start = 0
        for i in range(len(text) - 1):
            if _in_class(text[i], before, inner_ctx) and _in_class(
                text[i + 1], after, inner_ctx
            ):
                pieces.append(text[start : i + 1])
                start = i + 1
        pieces.append(text[start:])
        return pieces

    return _split


def _table_replace_value(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ReplaceValue", args, 5)
    table = _require_table(args[0])
    old_spec, new_spec, replacer = args[1], args[2], args[3]
    columns = _field_name_list(args[4])
    if table:
        for name in columns:
            if name not in table[0]:
                raise EvalError(f"Table.ReplaceValue: no such column: {name}")
    old_is_fn = _is_invocable(old_spec)
    new_is_fn = _is_invocable(new_spec)
    result: list[dict[str, Any]] = []
    for row in table:
        old_value = ctx.invoke(old_spec, [row], ctx) if old_is_fn else old_spec
        new_value = ctx.invoke(new_spec, [row], ctx) if new_is_fn else new_spec
        new_row = dict(row)
        for name in columns:
            new_row[name] = ctx.invoke(
                replacer, [new_row[name], old_value, new_value], ctx
            )
        result.append(new_row)
    return result


def _replacer_replace_value(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Replacer.ReplaceValue", args, 3)
    current, old, new = args[0], args[1], args[2]
    return new if _m_equal(current, old) else current


def _replacer_replace_text(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Replacer.ReplaceText", args, 3)
    current, old, new = args[0], args[1], args[2]
    if current is None:
        return None
    if not isinstance(current, str):
        raise EvalError("Replacer.ReplaceText: current value must be text")
    if not isinstance(old, str) or not isinstance(new, str):
        raise EvalError("Replacer.ReplaceText: old/new value must be text")
    return current.replace(old, new)


# --------------------------------------------------------------------------
# Everyday verbs
# --------------------------------------------------------------------------


def _table_skip(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Skip", args, 1, 2)
    table = _require_table(args[0])
    if len(args) == 1:
        return table[1:]
    spec = args[1]
    if _is_invocable(spec):
        i = 0
        while i < len(table):
            keep_skipping = ctx.invoke(spec, [table[i]], ctx)
            if not isinstance(keep_skipping, bool):
                raise EvalError("Table.Skip: condition must return a logical value")
            if not keep_skipping:
                break
            i += 1
        return table[i:]
    count = _require_int(spec)
    if count < 0:
        raise EvalError("Table.Skip: count must not be negative")
    return table[count:]


def _table_range(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Range", args, 2, 3)
    table = _require_table(args[0])
    offset = _require_int(args[1])
    if offset < 0:
        raise EvalError("Table.Range: offset must not be negative")
    if offset > len(table):
        raise EvalError("Table.Range: offset is out of range")
    if len(args) == 3:
        count = _require_int(args[2])
        if count < 0:
            raise EvalError("Table.Range: count must not be negative")
        if offset + count > len(table):
            raise EvalError("Table.Range: offset + count is out of range")
        return table[offset : offset + count]
    return table[offset:]


def _table_reorder_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ReorderColumns", args, 2, 3)
    if len(args) == 3 and args[2] is not None:
        raise UnsupportedError("Table.ReorderColumns: missingField option")
    table = _require_table(args[0])
    order = _field_name_list(args[1])
    header = _column_order(table)
    seen: set[str] = set()
    for name in order:
        if name in seen:
            raise EvalError(f"Table.ReorderColumns: duplicate column: {name}")
        seen.add(name)
        if table and name not in header:
            raise EvalError(f"Table.ReorderColumns: no such column: {name}")
    final_order = list(order) + [c for c in header if c not in seen]
    return [{name: row[name] for name in final_order} for row in table]


def _table_duplicate_column(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.DuplicateColumn", args, 3, 4)
    if len(args) == 4 and args[3] is not None:
        raise UnsupportedError("Table.DuplicateColumn: newColumnNames (4th argument)")
    table = _require_table(args[0])
    source = _require_str(args[1])
    new_name = _require_str(args[2])
    if table and source not in table[0]:
        raise EvalError(f"Table.DuplicateColumn: no such column: {source}")
    if table and new_name != source and new_name in table[0]:
        raise EvalError(f"Table.DuplicateColumn: column already exists: {new_name}")
    return [dict(row, **{new_name: row[source]}) for row in table]


def _table_combine(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Combine", args, 1, 2)
    if len(args) == 2 and args[1] is not None:
        raise UnsupportedError("Table.Combine: columns option")
    tables = _require_list(args[0])
    header: list[str] = []
    seen: set[str] = set()
    parsed_tables: list[list[dict[str, Any]]] = []
    for t in tables:
        rows = _require_table(t)
        parsed_tables.append(rows)
        if rows:
            for name in rows[0].keys():
                if name not in seen:
                    seen.add(name)
                    header.append(name)
    result: list[dict[str, Any]] = []
    for rows in parsed_tables:
        for row in rows:
            result.append({name: row.get(name) for name in header})
    return result


def _table_buffer(args: list[Any], ctx: _Ctx) -> Any:
    # Identity: this evaluator has no lazy/streaming table representation to
    # force - every table value is already fully materialised the moment it
    # exists (see the module docstring's data-model note) - but the UI
    # emits Table.Buffer routinely, so it must not error.
    _arity("Table.Buffer", args, 1)
    return list(_require_table(args[0]))


def _table_column_count(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ColumnCount", args, 1)
    table = _require_table(args[0])
    return len(table[0]) if table else 0


def _table_is_empty(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.IsEmpty", args, 1)
    return len(_require_table(args[0])) == 0


def _table_has_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.HasColumns", args, 2)
    table = _require_table(args[0])
    names = _field_name_list(args[1])
    header = set(table[0].keys()) if table else set()
    return all(name in header for name in names)


def _table_transform_column_names(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.TransformColumnNames", args, 2)
    table = _require_table(args[0])
    transform = args[1]
    if not table:
        return []
    original = list(table[0].keys())
    new_names: list[str] = []
    seen: set[str] = set()
    for name in original:
        new_name = ctx.invoke(transform, [name], ctx)
        if not isinstance(new_name, str):
            raise EvalError("Table.TransformColumnNames: transform must return text")
        if new_name in seen:
            raise EvalError(
                "Table.TransformColumnNames: duplicate resulting "
                f"column name: {new_name}"
            )
        seen.add(new_name)
        new_names.append(new_name)
    mapping = dict(zip(original, new_names, strict=True))
    return [{mapping[k]: v for k, v in row.items()} for row in table]


def _table_remove_rows_with_errors(args: list[Any], ctx: _Ctx) -> Any:
    # Identity, not a shortcut: this evaluator raises a Python exception the
    # instant a formula errors (evaluate.py's EvalError/UnsupportedError),
    # so evaluation never produces a table with an error VALUE sitting in a
    # cell in the first place - unlike real Power Query, where a per-row
    # formula failure can leave an error object in one cell without
    # aborting the whole column. There is structurally no "row with an
    # error" this function could ever remove in this data model, so
    # returning the table unchanged is the exact answer, not an
    # approximation of a connector.
    _arity("Table.RemoveRowsWithErrors", args, 1, 2)
    return list(_require_table(args[0]))


def _table_max_or_min(args: list[Any], want_max: bool, what: str) -> Any:
    _arity(what, args, 2, 3)
    table = _require_table(args[0])
    default = args[2] if len(args) == 3 else None
    if not table:
        return default
    keys = _parse_comparison_keys(args[1], what)
    for name, _ in keys:
        if name not in table[0]:
            raise EvalError(f"{what}: no such column: {name}")
    rows = list(table)
    try:
        # Stable sort, least-significant key first (mirrors _table_sort in
        # _table.py); Max/Min are then just the last/first row after that
        # sort - a Descending direction on a key flips which real value
        # counts as "biggest" for that key, exactly as it does for Sort.
        for name, descending in reversed(keys):
            rows.sort(key=lambda row: row[name], reverse=descending)
    except TypeError as error:
        raise EvalError(f"{what}: values are not comparable") from error
    return rows[-1] if want_max else rows[0]


def _table_max(args: list[Any], ctx: _Ctx) -> Any:
    return _table_max_or_min(args, want_max=True, what="Table.Max")


def _table_min(args: list[Any], ctx: _Ctx) -> Any:
    return _table_max_or_min(args, want_max=False, what="Table.Min")


# --------------------------------------------------------------------------
# From/To conversions
# --------------------------------------------------------------------------


def _table_from_list(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FromList", args, 1, 5)
    items = _require_list(args[0])
    splitter = args[1] if len(args) >= 2 else None
    columns_spec = args[2] if len(args) >= 3 else None
    default = args[3] if len(args) >= 4 else None
    extra = args[4] if len(args) >= 5 and args[4] is not None else "ExtraValues.Ignore"
    if extra not in ("ExtraValues.Ignore", "ExtraValues.Error", "ExtraValues.List"):
        raise UnsupportedError(f"Table.FromList: extraValues {extra!r}")

    def default_splitter(item: Any) -> Any:
        if not isinstance(item, str):
            raise EvalError("Table.FromList: default splitter requires text items")
        return item.split(",")

    if splitter is None:
        split_fn = default_splitter
    elif _is_invocable(splitter):
        split_fn = lambda item: ctx.invoke(splitter, [item], ctx)  # noqa: E731
    else:
        raise EvalError("Table.FromList: splitter must be a function or null")

    split_values: list[list[Any] | None] = []
    for item in items:
        if item is None:
            split_values.append(None)
            continue
        pieces = split_fn(item)
        if not isinstance(pieces, list):
            raise EvalError("Table.FromList: splitter must return a list")
        split_values.append(pieces)

    column_names: list[str]
    if columns_spec is None:
        slot_count = max((len(p) for p in split_values if p is not None), default=1)
        column_names = [f"Column{i + 1}" for i in range(max(slot_count, 1))]
    elif isinstance(columns_spec, list):
        column_names = [_require_str(n) for n in columns_spec]
        if not column_names:
            raise EvalError("Table.FromList: columns list must not be empty")
    elif isinstance(columns_spec, (int, float)) and not isinstance(columns_spec, bool):
        count = _require_int(columns_spec)
        if count < 1:
            raise EvalError("Table.FromList: columns count must be at least 1")
        column_names = [f"Column{i + 1}" for i in range(count)]
    else:
        raise UnsupportedError("Table.FromList: columns as a table type")

    return [
        _distribute_pieces(pieces, column_names, default, extra, "Table.FromList")
        for pieces in split_values
    ]


def _table_to_list(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ToList", args, 1, 2)
    table = _require_table(args[0])
    combiner = args[1] if len(args) == 2 else None
    if combiner is None:
        header = _column_order(table)
        if len(header) > 1:
            raise EvalError(
                "Table.ToList: a table with more than one column needs a combiner"
            )
        return [next(iter(row.values())) if row else None for row in table]
    if not _is_invocable(combiner):
        raise EvalError("Table.ToList: combiner must be a function")
    return [ctx.invoke(combiner, [list(row.values())], ctx) for row in table]


def _table_from_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FromColumns", args, 1, 2)
    columns = [_require_list(c) for c in _require_list(args[0])]
    columns_spec = args[1] if len(args) == 2 else None
    names: list[str]
    if columns_spec is None:
        names = [f"Column{i + 1}" for i in range(len(columns))]
    elif isinstance(columns_spec, list):
        names = [_require_str(n) for n in columns_spec]
        if len(names) != len(columns):
            raise EvalError(
                "Table.FromColumns: columns list length must match the number of "
                "column-lists"
            )
    else:
        raise UnsupportedError("Table.FromColumns: columns as a table type")
    height = max((len(c) for c in columns), default=0)
    result: list[dict[str, Any]] = []
    for r in range(height):
        row: dict[str, Any] = {}
        for name, col in zip(names, columns, strict=True):
            row[name] = col[r] if r < len(col) else None
        result.append(row)
    return result


def _table_to_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ToColumns", args, 1)
    table = _require_table(args[0])
    header = _column_order(table)
    return [[row[name] for row in table] for name in header]


def _table_from_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FromRows", args, 1, 2)
    rows = [_require_list(r) for r in _require_list(args[0])]
    columns_spec = args[1] if len(args) == 2 else None
    column_names: list[str]
    if columns_spec is None:
        width = max((len(r) for r in rows), default=0)
        column_names = [f"Column{i + 1}" for i in range(width)]
    elif isinstance(columns_spec, list):
        column_names = [_require_str(n) for n in columns_spec]
    elif isinstance(columns_spec, (int, float)) and not isinstance(columns_spec, bool):
        column_names = [f"Column{i + 1}" for i in range(_require_int(columns_spec))]
    else:
        from ._type import _MType

        names = getattr(columns_spec, "field_names", None)
        if isinstance(columns_spec, _MType) and names:
            column_names = list(names)
        else:
            raise UnsupportedError(
                "Table.FromRows: columns as a type without named fields"
            )
    result: list[dict[str, Any]] = []
    for r in rows:
        row: dict[str, Any] = {}
        for i, name in enumerate(column_names):
            row[name] = r[i] if i < len(r) else None
        result.append(row)
    return result


def _hash_table(args: list[Any], ctx: _Ctx) -> Any:
    """``#table(columns, rows)`` - M's table literal.

    This is the same construction ``Table.FromRows`` performs with its two
    arguments in the other order, so it delegates rather than growing a second
    copy of the column-spec handling (list of names / a count / a table type).
    ``null`` columns means "name them Column1..N", which is what
    ``Table.FromRows`` already does when the spec is omitted.
    """
    _arity("#table", args, 2)
    columns, rows = args
    return _table_from_rows([rows] if columns is None else [rows, columns], ctx)


def _table_column(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.Column(table, column)`` - one column's values as a list.

    The list-shaped counterpart to ``Table.SelectColumns``, and the way a
    navigation step reaches a nested table: ``Table.Column(Source, "Data"){0}``.
    """
    _arity("Table.Column", args, 2)
    table = _require_table(args[0])
    column = _require_str(args[1])
    if table and column not in table[0]:
        raise EvalError(f"Table.Column: column not found: {column}")
    return [row.get(column) for row in table]


def _table_to_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ToRows", args, 1)
    table = _require_table(args[0])
    return [list(row.values()) for row in table]


def _table_from_value(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FromValue", args, 1, 2)
    value = args[0]
    options = args[1] if len(args) == 2 else None
    column_name = "Value"
    if options is not None:
        remaining = dict(_require_record(options))
        if "DefaultColumnName" in remaining:
            column_name = _require_str(remaining.pop("DefaultColumnName"))
        if remaining:
            raise UnsupportedError(f"Table.FromValue: option(s) {sorted(remaining)}")
    if isinstance(value, list):
        # This tool's data model represents both "a table" and "a list of
        # records" as list[dict] with no separate type tag (evaluate.py's
        # module docstring), so a list of records is treated as already
        # being a table (identity) rather than wrapped one-record-per-cell
        # under a "Value" column - the same modelling choice the rest of
        # this codebase already makes everywhere else (e.g. Table.
        # FromRecords/ToRecords), not a new judgement call introduced here.
        if value and all(isinstance(item, dict) for item in value):
            return list(value)
        return [{column_name: item} for item in value]
    if isinstance(value, dict):
        return [dict(value)]
    return [{column_name: value}]


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.


def _table_reverse_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ReverseRows", args, 1)
    return list(reversed(_require_table(args[0])))


def _table_repeat(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Repeat", args, 2)
    rows = _require_table(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError(f"Table.Repeat: count must not be negative, got {count}")
    return [dict(row) for _ in range(count) for row in rows]


def _table_expand_list_column(args: list[Any], ctx: _Ctx) -> Any:
    # An empty list KEEPS its row and nulls the cell - it does not drop the
    # row. That is the documented behaviour of the left-outer-join-then-expand
    # pattern, where an unmatched row survives with nulls rather than
    # vanishing, and getting it backwards would silently delete real rows.
    # A nested table needs no separate branch: the docs define expanding one
    # as "treating them as lists of records", and a table already IS a list
    # of records in this data model.
    _arity("Table.ExpandListColumn", args, 2)
    rows = _require_table(args[0])
    column = _require_str(args[1])
    out: list[dict[str, Any]] = []
    for row in rows:
        if column not in row:
            raise EvalError(f"Table.ExpandListColumn: column not found: {column}")
        value = row[column]
        if value is None or (isinstance(value, list) and not value):
            # null is folded into the empty-list case deliberately: both yield
            # one row with a null cell, so neither can lose a row. Power
            # Query's exact behaviour for a literal null here is not stated in
            # the docs, and dropping the row would be the unrecoverable guess.
            out.append({**row, column: None})
        elif isinstance(value, list):
            out.extend({**row, column: item} for item in value)
        else:
            raise EvalError(
                "Table.ExpandListColumn: expected a list in column "
                f"{column}, got {_type_name(value)}"
            )
    return out


def _table_select_rows_with_errors(args: list[Any], ctx: _Ctx) -> Any:
    # The exact dual of Table.RemoveRowsWithErrors above, and honest for the
    # same reason: this evaluator raises the instant a formula errors, so an
    # error value can never come to rest in a cell. There is structurally no
    # row with an error to select, so the empty table is the exact answer -
    # not an approximation, and not a silent pass-through of every row, which
    # is the failure mode that would matter.
    _arity("Table.SelectRowsWithErrors", args, 1, 2)
    _require_table(args[0])
    if len(args) == 2:
        _field_name_list(args[1])
    return []


def _table_replace_error_values(args: list[Any], ctx: _Ctx) -> Any:
    # Identity, for the same data-model reason as Table.RemoveRowsWithErrors:
    # no error value can exist in a cell, so there is nothing to replace. The
    # replacement list is still validated, so a malformed call fails loudly
    # here rather than appearing to work and diverging from Power Query.
    _arity("Table.ReplaceErrorValues", args, 2)
    rows = _require_table(args[0])
    for pair in _require_list(args[1]):
        entry = _require_list(pair)
        if len(entry) != 2:
            raise EvalError(
                "Table.ReplaceErrorValues: each replacement must be "
                f"{{column, value}}, got {len(entry)} item(s)"
            )
        _require_str(entry[0])
    return [dict(row) for row in rows]


BUILTINS: dict[str, Any] = {
    "Table.Unpivot": _table_unpivot,
    "Table.UnpivotOtherColumns": _table_unpivot_other_columns,
    "Table.Pivot": _table_pivot,
    "Table.Transpose": _table_transpose,
    "Table.FillDown": _table_fill_down,
    "Table.FillUp": _table_fill_up,
    "Table.AddIndexColumn": _table_add_index_column,
    "Table.SplitColumn": _table_split_column,
    "Splitter.SplitTextByDelimiter": _splitter_split_text_by_delimiter,
    "Splitter.SplitTextByEachDelimiter": _splitter_split_text_by_each_delimiter,
    "Splitter.SplitTextByPositions": _splitter_split_text_by_positions,
    "Splitter.SplitTextByCharacterTransition": (
        _splitter_split_text_by_character_transition
    ),
    "Table.ReplaceValue": _table_replace_value,
    "Replacer.ReplaceText": _replacer_replace_text,
    "Replacer.ReplaceValue": _replacer_replace_value,
    "Table.Skip": _table_skip,
    "Table.Range": _table_range,
    "Table.ReorderColumns": _table_reorder_columns,
    "Table.DuplicateColumn": _table_duplicate_column,
    "Table.Combine": _table_combine,
    "Table.Buffer": _table_buffer,
    "Table.ColumnCount": _table_column_count,
    "Table.IsEmpty": _table_is_empty,
    "Table.HasColumns": _table_has_columns,
    "Table.TransformColumnNames": _table_transform_column_names,
    "Table.RemoveRowsWithErrors": _table_remove_rows_with_errors,
    # There is deliberately no `Table.SelectDuplicates`. pqtools shipped one
    # until 0.10.0, but it does not appear in Microsoft's table of the ~114
    # real Table.* functions (learn.microsoft.com/en-us/powerquery-m/
    # table-functions) and its own reference page is a 404 while a real page
    # like table-selectrows is a 200. Table.Distinct (keep one row per key)
    # and Table.Group (count/collect occurrences per key) already cover
    # every real use a "give me the duplicates" query has.
    "Table.Max": _table_max,
    "Table.Min": _table_min,
    "Table.FromList": _table_from_list,
    "Table.ToList": _table_to_list,
    "Table.FromColumns": _table_from_columns,
    "Table.ToColumns": _table_to_columns,
    "Table.FromRows": _table_from_rows,
    "Table.Column": _table_column,
    "#table": _hash_table,
    "Table.ToRows": _table_to_rows,
    "Table.FromValue": _table_from_value,
    "Table.ReverseRows": _table_reverse_rows,
    "Table.Repeat": _table_repeat,
    "Table.ExpandListColumn": _table_expand_list_column,
    "Table.SelectRowsWithErrors": _table_select_rows_with_errors,
    "Table.ReplaceErrorValues": _table_replace_error_values,
    # Enum-like bare identifiers - see the module docstring's "Enum-like
    # bare identifiers" note for why these are registered directly here
    # (enum resolution now lives in _enums.py - see its docstring)
    "QuoteStyle.Csv": "QuoteStyle.Csv",
    "QuoteStyle.None": "QuoteStyle.None",
    "ExtraValues.Ignore": "ExtraValues.Ignore",
    "ExtraValues.Error": "ExtraValues.Error",
    "ExtraValues.List": "ExtraValues.List",
}


# --------------------------------------------------------------------------
# Row-set predicates, slicing, and schema - the Table.* gaps found by
# checking the registry against the functions real queries actually use.
# --------------------------------------------------------------------------


def _table_contains(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Contains", args, 2, 3)
    table = _require_table(args[0])
    row = _require_record(args[1])
    return any(all(_m_equal(r.get(k), v) for k, v in row.items()) for r in table)


def _table_position_of(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.PositionOf(table, row, occurrence)`` - -1 when absent, as M does."""
    _arity("Table.PositionOf", args, 2, 4)
    table = _require_table(args[0])
    row = _require_record(args[1])
    hits = [
        index
        for index, r in enumerate(table)
        if all(_m_equal(r.get(k), v) for k, v in row.items())
    ]
    if not hits:
        return -1
    occurrence = args[2] if len(args) >= 3 else None
    if occurrence is None or occurrence == 0:
        return hits[0]
    if occurrence == 1:
        return hits[-1]
    return hits


def _row_predicate(name: str, predicate: Any, row: Any, ctx: _Ctx) -> bool:
    """Run a row predicate, insisting on a logical result.

    Table.SelectRows sets this convention: a predicate that returns a non
    logical is a bug in the query, and coercing it would hide the bug behind
    a plausible row count.
    """
    keep = ctx.invoke(predicate, [row], ctx)
    if not isinstance(keep, bool):
        raise EvalError(f"{name}: condition must return a logical value")
    return keep


def _table_matches_any_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.MatchesAnyRows", args, 2)
    table = _require_table(args[0])
    return any(
        _row_predicate("Table.MatchesAnyRows", args[1], row, ctx) for row in table
    )


def _table_matches_all_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.MatchesAllRows", args, 2)
    table = _require_table(args[0])
    return all(
        _row_predicate("Table.MatchesAllRows", args[1], row, ctx) for row in table
    )


def _table_remove_matching_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.RemoveMatchingRows", args, 2, 3)
    table = _require_table(args[0])
    targets = [_require_record(r) for r in _require_list(args[1])]
    equal: Callable[[Any, Any], bool] = _m_equal
    if len(args) == 3 and args[2] is not None:
        # The third argument was accepted and then dropped on the floor, so
        # the docs' own example - removing a "widget" row from a table
        # holding "Widget" with Comparer.OrdinalIgnoreCase - kept the row it
        # was called to remove. A wrong table, no error anywhere.
        equal = _equation_criteria_predicate(
            args[2], ctx, "Table.RemoveMatchingRows", allow_custom_comparer=True
        )
    return [
        row
        for row in table
        if not any(all(equal(row.get(k), v) for k, v in t.items()) for t in targets)
    ]


def _table_insert_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.InsertRows", args, 3)
    table = _require_table(args[0])
    offset = _require_int(args[1])
    if not 0 <= offset <= len(table):
        raise EvalError(f"Table.InsertRows: offset {offset} is outside the table")
    rows = [_require_record(r) for r in _require_list(args[2])]
    return [*table[:offset], *rows, *table[offset:]]


def _table_split_at(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.SplitAt(table, count)`` - the two halves, as a list of tables."""
    _arity("Table.SplitAt", args, 2)
    table = _require_table(args[0])
    count = _require_int(args[1])
    return [table[:count], table[count:]]


def _table_alternate_rows(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.AlternateRows(table, offset, skip, take)``: drop then keep, repeating."""
    _arity("Table.AlternateRows", args, 4)
    table = _require_table(args[0])
    offset, skip, take = (_require_int(a) for a in args[1:4])
    if skip < 0 or take < 0:
        raise EvalError("Table.AlternateRows: skip and take must not be negative")
    kept = list(table[:offset])
    index = offset
    period = skip + take
    if period == 0:
        return kept + list(table[offset:])
    while index < len(table):
        index += skip
        kept.extend(table[index : index + take])
        index += take
    return kept


def _table_schema(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.Schema(table)`` - one row per column.

    Only the fields this evaluator can actually determine are filled in. The
    rest are null rather than guessed: a schema row claiming a precision or a
    nominal type it never saw would be read as fact.
    """
    _arity("Table.Schema", args, 1)
    table = _require_table(args[0])
    rows = []
    for position, name in enumerate(_column_order(table)):
        kinds = {
            _type_name(row.get(name)) for row in table if row.get(name) is not None
        }
        kind = kinds.pop() if len(kinds) == 1 else "any"
        rows.append(
            {
                "Name": name,
                "Position": position,
                "TypeName": kind,
                "Kind": kind,
                "IsNullable": any(row.get(name) is None for row in table),
                "NumericPrecision": None,
                "NumericScale": None,
                "NativeTypeName": None,
                "Description": None,
            }
        )
    return rows


def _table_profile(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.Profile(table)`` - per-column summary statistics."""
    _arity("Table.Profile", args, 1, 2)
    table = _require_table(args[0])
    rows = []
    for name in _column_order(table):
        values = [row.get(name) for row in table]
        present = [v for v in values if v is not None]
        numbers = [
            v
            for v in present
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        ordered = sorted(numbers) if numbers else []
        rows.append(
            {
                "Column": name,
                "Min": (
                    min(ordered) if ordered else (min(present) if present else None)
                ),
                "Max": (
                    max(ordered) if ordered else (max(present) if present else None)
                ),
                "Average": (sum(numbers) / len(numbers)) if numbers else None,
                "StandardDeviation": _stdev(numbers),
                "Count": len(values),
                "NullCount": len(values) - len(present),
                "DistinctCount": len({_hashable(v) for v in values}),
            }
        )
    return rows


def _stdev(numbers: list[Any]) -> Any:
    if len(numbers) < 2:
        return None
    mean = sum(numbers) / len(numbers)
    return (sum((n - mean) ** 2 for n in numbers) / (len(numbers) - 1)) ** 0.5


def _hashable(value: Any) -> Any:
    """A hashable stand-in, so DistinctCount can use a set on any column."""
    if isinstance(value, (list, dict)):
        return repr(value)
    return value


BUILTINS.update(
    {
        "Table.Contains": _table_contains,
        "Table.PositionOf": _table_position_of,
        "Table.MatchesAnyRows": _table_matches_any_rows,
        "Table.MatchesAllRows": _table_matches_all_rows,
        "Table.RemoveMatchingRows": _table_remove_matching_rows,
        "Table.InsertRows": _table_insert_rows,
        "Table.SplitAt": _table_split_at,
        "Table.AlternateRows": _table_alternate_rows,
        "Table.Schema": _table_schema,
        "Table.Profile": _table_profile,
    }
)


# --------------------------------------------------------------------------
# Table.FindText / Table.PrefixColumns / Table.CombineColumns* / Table.Split /
# Table.TransformRows - the 0.10.0 gap-fill batch (46 documented names this
# package did not have). Grounded against learn.microsoft.com/en-us/
# powerquery-m/<name-lowercased>, one page per function.
# --------------------------------------------------------------------------


def _table_find_text(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.FindText(table, text)`` - rows containing ``text`` anywhere.

    Only text-typed cells are searched, with a case-sensitive substring
    match. Microsoft's own page has exactly one worked example and it uses
    an all-text table - it states no stringification rule for numbers/dates
    and no case-sensitivity rule at all. Coercing every cell through a
    Text.From-style conversion to widen the search would be inventing
    behaviour this page never describes; the narrower, literal reading is
    the safer wrong-to-be-caught choice. Pinned by a test naming this as a
    choice, not a verified fact.
    """
    _arity("Table.FindText", args, 2)
    table = _require_table(args[0])
    text = _require_str(args[1])
    return [
        row
        for row in table
        if any(isinstance(v, str) and text in v for v in row.values())
    ]


def _table_prefix_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.PrefixColumns", args, 2)
    table = _require_table(args[0])
    prefix = _require_str(args[1])
    return [{f"{prefix}.{name}": value for name, value in row.items()} for row in table]


def _table_columns_of_type(args: list[Any], ctx: _Ctx) -> Any:
    """Refused - not approximated.

    Microsoft's own worked example ascribes a DECLARED table type
    (``type table[a=Number.Type, b=Text.Type]``) and matches ``listOfTypes``
    against that declared type, independent of what the rows actually hold.
    This evaluator's tables are plain ``list[dict[str, Any]]`` with no
    per-column declared type attached (see the module docstring) - every
    table here is implicitly ``any``-typed in the sense the real function
    cares about. Checking each cell's *runtime* kind instead would silently
    disagree with real Power Query on the ordinary case of an unascribed
    table (e.g. every column of numbers would "match" ``type number`` here
    but not in Power Query, where an unascribed column is typed ``any``) -
    exactly the plausible-wrong-answer failure this package refuses to ship.
    """
    _arity("Table.ColumnsOfType", args, 2)
    raise UnsupportedError(
        "Table.ColumnsOfType: matches a column's DECLARED type, which this "
        "evaluator's tables do not carry independent of their row data - "
        "see the function's docstring for why a runtime-value guess would "
        "silently disagree with real Power Query"
    )


def _table_combine_columns(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.CombineColumns(table, sourceColumns, combiner, column)``.

    The combined column lands at the position of the FIRST source column,
    and every source column is removed - not directly shown by Microsoft's
    2-column worked example (which cannot distinguish a position among only
    two total columns), but the same convention this file's own
    ``Table.SplitColumn`` already uses for its inverse operation (replace
    the source column(s) in place), applied here for consistency rather
    than invented fresh.
    """
    _arity("Table.CombineColumns", args, 4)
    table = _require_table(args[0])
    source_columns = _field_name_list(args[1])
    combiner = args[2]
    new_column = _require_str(args[3])
    if not source_columns:
        raise EvalError("Table.CombineColumns: sourceColumns must not be empty")
    header = _column_order(table)
    for name in source_columns:
        if table and name not in header:
            raise EvalError(f"Table.CombineColumns: no such column: {name}")
    if new_column != source_columns[0] and new_column in header:
        raise EvalError(f"Table.CombineColumns: column already exists: {new_column}")
    result: list[dict[str, Any]] = []
    for row in table:
        combined = ctx.invoke(combiner, [[row[name] for name in source_columns]], ctx)
        new_row: dict[str, Any] = {}
        for key in header:
            if key == source_columns[0]:
                new_row[new_column] = combined
            elif key not in source_columns:
                new_row[key] = row[key]
        result.append(new_row)
    return result


def _table_combine_columns_to_record(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.CombineColumnsToRecord(table, newColumnName, sourceColumns, options?)``.

    Microsoft's page has no worked example at all (Syntax + About only), so
    two things are inferred rather than verified: source columns are
    removed and the new column takes their first position - mirroring the
    sibling ``Table.CombineColumns`` above, which this function is
    otherwise identical to except for producing a record instead of running
    a combiner. ``options`` (``DisplayNameColumn``/``TypeName``) are
    presentation/data-load metadata ("used during data load to drive
    behavior by the loading environment") with no stated effect on the
    record's VALUE in a headless evaluator - validated, not applied.
    """
    _arity("Table.CombineColumnsToRecord", args, 3, 4)
    table = _require_table(args[0])
    new_column = _require_str(args[1])
    source_columns = _field_name_list(args[2])
    options = args[3] if len(args) == 4 else None
    if options is not None:
        remaining = dict(_require_record(options))
        remaining.pop("DisplayNameColumn", None)
        remaining.pop("TypeName", None)
        if remaining:
            raise UnsupportedError(
                f"Table.CombineColumnsToRecord: option(s) {sorted(remaining)}"
            )
    if not source_columns:
        raise EvalError("Table.CombineColumnsToRecord: sourceColumns must not be empty")
    header = _column_order(table)
    for name in source_columns:
        if table and name not in header:
            raise EvalError(f"Table.CombineColumnsToRecord: no such column: {name}")
    if new_column != source_columns[0] and new_column in header:
        raise EvalError(
            f"Table.CombineColumnsToRecord: column already exists: {new_column}"
        )
    result: list[dict[str, Any]] = []
    for row in table:
        record = {name: row[name] for name in source_columns}
        new_row: dict[str, Any] = {}
        for key in header:
            if key == source_columns[0]:
                new_row[new_column] = record
            elif key not in source_columns:
                new_row[key] = row[key]
        result.append(new_row)
    return result


def _table_split(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.Split(table, pageSize)`` - a list of tables, ``pageSize`` rows
    each; the last chunk is short rather than padded or errored (Microsoft's
    own 5-rows/pageSize=2 worked example ends in a 1-row table)."""
    _arity("Table.Split", args, 2)
    table = _require_table(args[0])
    page_size = _require_int(args[1])
    if page_size <= 0:
        raise EvalError("Table.Split: pageSize must be a positive number")
    return [table[i : i + page_size] for i in range(0, len(table), page_size)]


def _table_transform_rows(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.TransformRows(table, transform)`` - a LIST, not a table
    (List.Transform over the table's rows-as-records; Microsoft's own two
    examples return a bare list of scalars and a bare list of records,
    never a table)."""
    _arity("Table.TransformRows", args, 2)
    table = _require_table(args[0])
    transform = args[1]
    return [ctx.invoke(transform, [row], ctx) for row in table]


# --------------------------------------------------------------------------
# Table.AddKey / Table.Keys / Table.ReplaceKeys / Table.PartitionKey /
# Table.ReplacePartitionKey - value-level metadata attached to a table.
#
# Real Power Query keys/partition-keys are declared on a table's static
# TYPE. This evaluator's tables are plain ``list[dict[str, Any]]`` with no
# type object attached at all (see the module docstring) - there is no
# existing channel to store "this table has these keys" independent of its
# rows. Rather than fabricate connector/type-system behaviour this
# evaluator cannot have, ``_KeyedTable`` attaches the metadata to the
# specific list OBJECT ``Table.AddKey``/``Table.ReplaceKeys``/
# ``Table.ReplacePartitionKey`` return; the getters read it off the object
# they are given. Metadata does NOT propagate through an unrelated
# transform building a fresh list (Table.Sort, Table.SelectRows, ...) -
# nothing documented claims it should, and this evaluator has no type
# checker to justify inventing that propagation. An ordinary list is simply
# "no keys declared" / "no partition key declared", exactly like a table
# Table.AddKey was never called on. Verified against learn.microsoft.com's
# own worked examples for Table.AddKey/Table.Keys/Table.ReplaceKeys (all
# three round-trip through a plain ``Table.FromRecords`` table with no
# connector involved, confirming this is a value-level operation, not a
# folding/connector one) - Table.PartitionKey/Table.ReplacePartitionKey have
# no worked example on Microsoft's site at all, so their opaque-list
# storage is a deliberate, narrower choice: store exactly what is given,
# interpret nothing, since no documented function is ever shown reading the
# partition key's internal shape.
# --------------------------------------------------------------------------


class _KeyedTable(list[dict[str, Any]]):
    """A table value that also carries AddKey/ReplaceKeys/
    ReplacePartitionKey metadata. See the section banner above."""

    __slots__ = ("pq_keys", "pq_partition_key")

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(rows)
        self.pq_keys: list[dict[str, Any]] = []
        self.pq_partition_key: Any = None


def _key_spec_list(value: Any, what: str) -> list[dict[str, Any]]:
    """``{[Columns = {...}, Primary = true/false], ...}`` - the exact shape
    ``Table.Keys`` returns and ``Table.ReplaceKeys`` accepts back, per
    Microsoft's own worked examples for both."""
    specs = _require_list(value)
    result: list[dict[str, Any]] = []
    for item in specs:
        record = _require_record(item)
        if "Columns" not in record or "Primary" not in record:
            raise EvalError(
                f"{what}: expected a list of [Columns = ..., Primary = ...] records"
            )
        columns = _field_name_list(record["Columns"])
        primary = record["Primary"]
        if not isinstance(primary, bool):
            raise EvalError(f"{what}: Primary must be a logical value")
        result.append({"Columns": columns, "Primary": primary})
    return result


def _table_add_key(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.AddKey", args, 3)
    rows = _require_table(args[0])
    columns = _field_name_list(args[1])
    is_primary = args[2]
    if not isinstance(is_primary, bool):
        raise EvalError("Table.AddKey: isPrimary must be a logical value")
    result = _KeyedTable(rows)
    if isinstance(rows, _KeyedTable):
        result.pq_keys = list(rows.pq_keys)
        result.pq_partition_key = rows.pq_partition_key
    result.pq_keys.append({"Columns": columns, "Primary": is_primary})
    return result


def _table_keys(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Keys", args, 1)
    rows = _require_table(args[0])
    if isinstance(rows, _KeyedTable):
        return list(rows.pq_keys)
    return []


def _table_replace_keys(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ReplaceKeys", args, 2)
    rows = _require_table(args[0])
    keys = _key_spec_list(args[1], "Table.ReplaceKeys")
    result = _KeyedTable(rows)
    if isinstance(rows, _KeyedTable):
        result.pq_partition_key = rows.pq_partition_key
    result.pq_keys = keys
    return result


def _table_partition_key(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.PartitionKey", args, 1)
    rows = _require_table(args[0])
    if isinstance(rows, _KeyedTable):
        return rows.pq_partition_key
    return None


def _table_replace_partition_key(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ReplacePartitionKey", args, 2)
    rows = _require_table(args[0])
    partition_key = args[1]
    if partition_key is not None and not isinstance(partition_key, list):
        raise EvalError(
            "Table.ReplacePartitionKey: partitionKey must be a list or null"
        )
    result = _KeyedTable(rows)
    if isinstance(rows, _KeyedTable):
        result.pq_keys = list(rows.pq_keys)
    result.pq_partition_key = partition_key
    return result


def _table_from_partitions(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.FromPartitions(partitionColumn, partitions, partitionColumnType?)``.

    ``partitions`` is a list of ``{value, table}`` pairs; each pair's rows
    are flattened into the result with ``partitionColumn`` = that pair's
    value added to every row. Nesting (a partition's own table built by a
    recursive ``Table.FromPartitions`` call) needs no special handling here
    - it is just an M-level function call producing an ordinary table by
    the time this function sees it. Verified against Microsoft's own
    3-level nested worked example (Year -> Month -> Day), which this
    reasoning reproduces exactly.
    """
    _arity("Table.FromPartitions", args, 2, 3)
    column = _require_str(args[0])
    partitions = _require_list(args[1])
    if len(args) == 3 and args[2] is not None:
        from ._type import _MType

        if not isinstance(args[2], _MType):
            raise EvalError(
                "Table.FromPartitions: expected a type value for "
                f"partitionColumnType, got {_type_name(args[2])}"
            )
        # "The type of the column defaults to any, but can be specified" -
        # a type ASCRIPTION, with no worked example showing it coerce
        # values (unlike Table.AddColumn's 4th argument, documented
        # elsewhere as an active TransformColumnTypes-style conversion).
        # Validated, not applied - inventing a coercion this page never
        # describes would be worse than a no-op.
    result: list[dict[str, Any]] = []
    for entry in partitions:
        pair = _require_list(entry)
        if len(pair) != 2:
            raise EvalError(
                "Table.FromPartitions: each partition must be a {value, table} pair"
            )
        value, subtable = pair
        for row in _require_table(subtable):
            if column in row:
                raise EvalError(
                    f"Table.FromPartitions: column already exists: {column}"
                )
            new_row = dict(row)
            new_row[column] = value
            result.append(new_row)
    return result


def _table_partition(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.Partition(table, column, groups, hash)`` - splits ``table``
    into ``groups`` tables by ``hash(row[column]) mod groups``. Verified
    against Microsoft's own worked example (4 rows, 2 groups, identity
    hash)."""
    _arity("Table.Partition", args, 4)
    table = _require_table(args[0])
    column = _require_str(args[1])
    groups = _require_int(args[2])
    if groups < 1:
        raise EvalError("Table.Partition: groups must be at least 1")
    hash_fn = args[3]
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(groups)]
    for row in table:
        if column not in row:
            raise EvalError(f"Table.Partition: no such column: {column}")
        hash_value = _require_int(ctx.invoke(hash_fn, [row[column]], ctx))
        buckets[hash_value % groups].append(row)
    return buckets


def _table_approximate_row_count(args: list[Any], ctx: _Ctx) -> Any:
    """Refused - not approximated.

    Microsoft's own page: "Returns the approximate number of rows in the
    table, or an error if the data source doesn't support approximation."
    Approximation is a stated DATA-SOURCE capability (the page's own
    example is a SQL cardinality estimate). An in-memory, connector-less
    table has no such capability by construction, so the documented answer
    for this evaluator is the error branch, not ``Table.RowCount``'s exact
    count silently relabeled as an approximate one.
    """
    _arity("Table.ApproximateRowCount", args, 1)
    raise UnsupportedError(
        "Table.ApproximateRowCount: the underlying data source does not "
        "support approximation (pqtools has no connector to ask, and "
        "Microsoft's own docs define exactly this case as an error - "
        "returning Table.RowCount's exact count under an 'approximate' "
        "label would be the plausible-wrong-answer this package refuses "
        "to ship)"
    )


def _table_partition_values(args: list[Any], ctx: _Ctx) -> Any:
    """Refused - not approximated.

    Microsoft's own page: "Returns information about how a table is
    partitioned. A table is returned where each column is a partition
    column in the original table." This describes a data SOURCE's physical
    partitioning scheme (e.g. a partitioned SQL/Delta table) - it is a
    different concept from this file's own ``Table.Partition`` (a pure,
    in-memory hash-split with no partition metadata attached to its output)
    or ``Table.ReplacePartitionKey`` (an opaque, uninterpreted value). There
    is no connector here to report a physical partitioning scheme from.
    """
    _arity("Table.PartitionValues", args, 1)
    raise UnsupportedError(
        "Table.PartitionValues: reads a data source's physical partitioning "
        "scheme, which this evaluator's in-memory tables do not have and no "
        "connector here could ever produce"
    )


def _table_stop_folding(args: list[Any], ctx: _Ctx) -> Any:
    # "Prevents any downstream operations from being run against the
    # original source" - this evaluator never folds anything to begin with
    # (always eager, no connector to fold work into), so "folding stops
    # here" is already, vacuously true for every table value it holds.
    # Identity is the honest answer, not an approximation of one - the
    # same argument Table.Buffer makes above for the same reason. Like
    # Table.Buffer, this deliberately does NOT preserve _KeyedTable
    # metadata (keys/partition key never propagate through an unrelated
    # transform - see the class docstring above).
    _arity("Table.StopFolding", args, 1)
    return list(_require_table(args[0]))


BUILTINS.update(
    {
        "Table.FindText": _table_find_text,
        "Table.PrefixColumns": _table_prefix_columns,
        "Table.ColumnsOfType": _table_columns_of_type,
        "Table.CombineColumns": _table_combine_columns,
        "Table.CombineColumnsToRecord": _table_combine_columns_to_record,
        "Table.Split": _table_split,
        "Table.TransformRows": _table_transform_rows,
        "Table.AddKey": _table_add_key,
        "Table.Keys": _table_keys,
        "Table.ReplaceKeys": _table_replace_keys,
        "Table.PartitionKey": _table_partition_key,
        "Table.ReplacePartitionKey": _table_replace_partition_key,
        "Table.FromPartitions": _table_from_partitions,
        "Table.Partition": _table_partition,
        "Table.ApproximateRowCount": _table_approximate_row_count,
        "Table.PartitionValues": _table_partition_values,
        "Table.StopFolding": _table_stop_folding,
    }
)
