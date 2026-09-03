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
  (``_eval_identifier_expression``) resolves any unbound name by checking
  ``BUILTINS.get(name)`` *first*, before its one legacy special case
  (``_table.py``'s ``_ORDER_ENUM``, imported by name for
  ``Order.Ascending``/``Order.Descending``). Adding a second special-cased
  import to ``evaluate.py`` is out of this module's scope (it is a frozen
  file for this task), so the supported extension point is the one
  ``evaluate.py`` already checks first: register the constant directly in
  this module's own ``BUILTINS`` dict, keyed by its qualified name, holding
  a plain sentinel value nothing else in the system would produce by
  accident. See the bottom of this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    """Column-name(s) + direction, in the shapes Table.Sort/Max/Min accept.

    Duplicated in miniature from ``_table.py``'s ``_table_sort`` (same
    accepted shapes: a bare column name, a list of names, or
    ``{{"Col", Order.Ascending|Descending}}`` pairs) rather than imported,
    to keep this module self-contained while ``_table.py`` is edited
    elsewhere.
    """
    keys: list[tuple[str, bool]] = []
    entries = [spec] if isinstance(spec, str) else spec
    if not isinstance(entries, list):
        raise UnsupportedError(
            f"{what} with a {type(spec).__name__} comparisonCriteria"
        )
    for entry in entries:
        if isinstance(entry, str):
            keys.append((entry, False))
        elif (
            isinstance(entry, list)
            and 1 <= len(entry) <= 2
            and isinstance(entry[0], str)
        ):
            if len(entry) == 1:
                keys.append((entry[0], False))
            elif entry[1] in (0, 1):
                keys.append((entry[0], entry[1] == 1))
            else:
                raise UnsupportedError(
                    f"{what}: direction must be Order.Ascending or Order.Descending"
                )
        else:
            raise UnsupportedError(
                f"{what}: comparisonCriteria entries must be a column name or "
                '{"Column", Order.Ascending}'
            )
    return keys


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


def _table_select_duplicates(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.SelectDuplicates", args, 1, 2)
    table = _require_table(args[0])
    names: list[str] | None = None
    if len(args) == 2 and args[1] is not None:
        names = _field_name_list(args[1])
        if table:
            for name in names:
                if name not in table[0]:
                    raise EvalError(f"Table.SelectDuplicates: no such column: {name}")

    def key_of(row: dict[str, Any]) -> Any:
        if names is not None:
            return tuple(row.get(name) for name in names)
        return row

    group_keys: list[Any] = []
    counts: list[int] = []
    index_of_row: list[int] = []
    for row in table:
        key = key_of(row)
        try:
            idx = group_keys.index(key)
        except ValueError:
            idx = len(group_keys)
            group_keys.append(key)
            counts.append(0)
        counts[idx] += 1
        index_of_row.append(idx)
    # Every row belonging to a group of size >= 2 is kept (not just the
    # "extra" occurrences beyond the first) - the common, widely
    # corroborated understanding of Table.SelectDuplicates; pinned by a
    # test since Microsoft's own reference page for it was unreachable
    # while implementing this.
    return [
        row for row, idx in zip(table, index_of_row, strict=True) if counts[idx] >= 2
    ]


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
        raise UnsupportedError("Table.FromRows: columns as a table type")
    result: list[dict[str, Any]] = []
    for r in rows:
        row: dict[str, Any] = {}
        for i, name in enumerate(column_names):
            row[name] = r[i] if i < len(r) else None
        result.append(row)
    return result


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
    "Table.SelectDuplicates": _table_select_duplicates,
    "Table.Max": _table_max,
    "Table.Min": _table_min,
    "Table.FromList": _table_from_list,
    "Table.ToList": _table_to_list,
    "Table.FromColumns": _table_from_columns,
    "Table.ToColumns": _table_to_columns,
    "Table.FromRows": _table_from_rows,
    "Table.ToRows": _table_to_rows,
    "Table.FromValue": _table_from_value,
    # Enum-like bare identifiers - see the module docstring's "Enum-like
    # bare identifiers" note for why these are registered directly here
    # rather than via a second _ORDER_ENUM-style import into evaluate.py.
    "QuoteStyle.Csv": "QuoteStyle.Csv",
    "QuoteStyle.None": "QuoteStyle.None",
    "ExtraValues.Ignore": "ExtraValues.Ignore",
    "ExtraValues.Error": "ExtraValues.Error",
    "ExtraValues.List": "ExtraValues.List",
}
