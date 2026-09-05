"""``Table.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.

``Table.SelectRows``/``Table.AddColumn``/``Table.TransformColumns`` call
back into M lambdas via ``ctx.invoke`` rather than a module-level
``_invoke`` import - see the ``_Ctx.invoke`` docstring in ``evaluate.py``
for why.

``Order.Ascending``/``Order.Descending`` used to be a hard-coded
``_ORDER_ENUM`` dict here that ``evaluate.py`` imported by name. They now live
in ``_enums.py`` and resolve through the ordinary ``BUILTINS`` lookup, because
an enum is just a value in M.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _field_name_list,
    _m_equal,
    _require_int,
    _require_str,
    _require_table,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _table_from_records(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FromRecords", args, 1)
    return list(_require_table(args[0]))


def _table_to_records(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ToRecords", args, 1)
    return list(_require_table(args[0]))


def _table_row_count(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.RowCount", args, 1)
    return len(_require_table(args[0]))


def _table_column_names(args: list[Any], ctx: _Ctx) -> Any:
    # A table here carries no schema beyond its rows (spec: "a TABLE is a
    # list of dicts"), so an empty table has no column names to report -
    # not a guess, the necessary consequence of that data model.
    _arity("Table.ColumnNames", args, 1)
    table = _require_table(args[0])
    return list(table[0].keys()) if table else []


def _table_select_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.SelectRows", args, 2)
    predicate = args[1]
    result = []
    for row in _require_table(args[0]):
        keep = ctx.invoke(predicate, [row], ctx)
        if not isinstance(keep, bool):
            raise EvalError("Table.SelectRows: predicate must return a logical value")
        if keep:
            result.append(row)
    return result


def _table_select_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.SelectColumns", args, 2)
    names = _field_name_list(args[1])
    result = []
    for row in _require_table(args[0]):
        for name in names:
            if name not in row:
                raise EvalError(f"Table.SelectColumns: no such column: {name}")
        result.append({name: row[name] for name in names})
    return result


def _table_remove_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.RemoveColumns", args, 2)
    names = _field_name_list(args[1])
    result = []
    for row in _require_table(args[0]):
        for name in names:
            if name not in row:
                raise EvalError(f"Table.RemoveColumns: no such column: {name}")
        result.append({key: value for key, value in row.items() if key not in names})
    return result


def _column_pairs(value: Any, what: str) -> list[tuple[str, Any]]:
    """``{old, new}`` or ``{{old1, new1}, {old2, new2}, ...}``."""

    def is_pair(item: Any) -> bool:
        return isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)

    if not isinstance(value, list):
        raise EvalError(f"{what}: expected a {{column, value}} pair or a list of them")
    if is_pair(value):
        return [(value[0], value[1])]
    pairs: list[tuple[str, Any]] = []
    for item in value:
        if not is_pair(item):
            raise EvalError(
                f"{what}: expected a {{column, value}} pair or a list of them"
            )
        pairs.append((item[0], item[1]))
    return pairs


def _table_rename_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.RenameColumns", args, 2)
    table = _require_table(args[0])
    pairs = _column_pairs(args[1], "Table.RenameColumns")
    mapping: dict[str, str] = {}
    for old, new in pairs:
        if not isinstance(new, str):
            raise EvalError("Table.RenameColumns: new column name must be text")
        if table and old not in table[0]:
            raise EvalError(f"Table.RenameColumns: no such column: {old}")
        mapping[old] = new
    return [
        {mapping.get(key, key): value for key, value in row.items()} for row in table
    ]


def _table_add_column(args: list[Any], ctx: _Ctx) -> Any:
    # The UI emits the 4-argument form with a declared column type
    # (`Table.AddColumn(t, "Year", each Date.Year([D]), Int64.Type)`), so refusing
    # it would reject most real queries. The declared type is applied to the
    # generated values, exactly as Table.TransformColumnTypes would - accepting it
    # and ignoring it would silently produce a differently-typed column than
    # Power Query does.
    _arity("Table.AddColumn", args, 3, 4)
    table = _require_table(args[0])
    name = _require_str(args[1])
    generator = args[2]
    declared = args[3] if len(args) == 4 else None
    if table and name in table[0]:
        raise EvalError(f"Table.AddColumn: column already exists: {name}")
    convert = None
    if declared is not None:
        # Imported lazily: _type imports nothing from here, but keeping the import
        # inside the branch means the common 3-arg path never pays for it.
        from ._type import _converter_for, _MType

        if not isinstance(declared, _MType):
            raise EvalError(
                "Table.AddColumn: expected a type value for the column type, got "
                f"{_type_name(declared)}"
            )
        convert = _converter_for(declared)
    result = []
    for row in table:
        new_row = dict(row)
        value = ctx.invoke(generator, [row], ctx)
        if convert is not None:
            value = convert(value)
        new_row[name] = value
        result.append(new_row)
    return result


def _table_transform_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.TransformColumns", args, 2)
    table = _require_table(args[0])
    pairs = _column_pairs(args[1], "Table.TransformColumns")
    result = []
    for row in table:
        new_row = dict(row)
        for name, transform in pairs:
            if name not in new_row:
                raise EvalError(f"Table.TransformColumns: no such column: {name}")
            new_row[name] = ctx.invoke(transform, [new_row[name]], ctx)
        result.append(new_row)
    return result


# Order.Ascending / Order.Descending are M enum constants (0 and 1). Power Query's
# own UI emits `Table.Sort(t, {{"Col", Order.Ascending}})`, so without these the most
# common real-world sort is unusable.


def _table_sort(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Sort", args, 2)
    table = _require_table(args[0])
    spec = args[1]
    # Accepted shapes, all of which Power Query itself emits:
    #   "Col"                                  one column, ascending
    #   {"A", "B"}                             several columns, ascending
    #   {{"Col", Order.Descending}}            column with an explicit direction
    #   {{"A", Order.Ascending}, {"B", Order.Descending}}
    keys: list[tuple[str, bool]] = []
    entries = [spec] if isinstance(spec, str) else spec
    if not isinstance(entries, list):
        raise UnsupportedError(f"Table.Sort with a {type(spec).__name__} sort spec")
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
                    "Table.Sort direction must be Order.Ascending or Order.Descending"
                )
        else:
            raise UnsupportedError(
                "Table.Sort entries must be a column name or "
                '{"Column", Order.Ascending}'
            )
    for name, _ in keys:
        if table and name not in table[0]:
            raise EvalError(f"Table.Sort: no such column: {name}")
    rows = list(table)
    try:
        # Stable sort, least significant key first, so mixed directions work.
        for name, descending in reversed(keys):
            rows.sort(key=lambda row: row[name], reverse=descending)
    except TypeError as error:
        raise EvalError("Table.Sort: values are not comparable") from error
    return rows


def _table_first_n(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FirstN", args, 2)
    table = _require_table(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Table.FirstN: count must not be negative")
    return table[:count]


def _table_last_n(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.LastN", args, 2)
    table = _require_table(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Table.LastN: count must not be negative")
    return table[len(table) - count :] if count else []


def _is_invocable(value: Any) -> bool:
    """True if ``ctx.invoke(value, ...)`` can call ``value`` as an M function.

    Duplicated in miniature from ``_table_shape.py``'s own ``_is_invocable``
    (same reasoning: a ``_Lambda`` from ``evaluate.py`` cannot be imported
    here without a circular import - see that module's docstring) rather
    than imported, to keep this module self-contained while the sibling
    file is edited elsewhere - the same convention ``_table_shape.py``'s
    ``_parse_comparison_keys`` docstring already documents for this exact
    situation.
    """
    return callable(value) or (
        hasattr(value, "params") and hasattr(value, "body") and hasattr(value, "scope")
    )


def _table_first(args: list[Any], ctx: _Ctx) -> Any:
    # No worked example on Microsoft's own page covers "empty table, no
    # default" - mirrors this codebase's List.First (_list.py) for the same
    # undocumented corner: null, not an error. Pinned by a test naming it as
    # a choice, not a verified fact.
    _arity("Table.First", args, 1, 2)
    table = _require_table(args[0])
    if table:
        return table[0]
    return args[1] if len(args) == 2 else None


def _table_last(args: list[Any], ctx: _Ctx) -> Any:
    # Same undocumented-corner choice as Table.First above.
    _arity("Table.Last", args, 1, 2)
    table = _require_table(args[0])
    if table:
        return table[-1]
    return args[1] if len(args) == 2 else None


def _table_first_value(args: list[Any], ctx: _Ctx) -> Any:
    # Microsoft's page has no worked example at all for this one. "First
    # column of the first row" reads unambiguously as: take row 0, then its
    # first field in column order. A table whose first row happens to have
    # zero fields is not addressed anywhere either - falling through to the
    # same default/null path as an empty table is the choice made here
    # (there is no "first column" to return in either case), not a verified
    # documented rule.
    _arity("Table.FirstValue", args, 1, 2)
    table = _require_table(args[0])
    if table and table[0]:
        return next(iter(table[0].values()))
    return args[1] if len(args) == 2 else None


def _table_single_row(args: list[Any], ctx: _Ctx) -> Any:
    # Microsoft's page declares the return type as plain `record` (not
    # `nullable record`) and takes no default argument - unlike Table.First/
    # Table.Last's `as any` with an optional default. Docs only state the
    # error for >1 row; 0 rows is unaddressed. Erroring on 0 rows too is the
    # reading forced by that non-nullable return type: there is no record
    # this function could honestly return for an empty table. Pinned by a
    # test naming it as a choice.
    _arity("Table.SingleRow", args, 1)
    table = _require_table(args[0])
    if len(table) != 1:
        raise EvalError(f"Table.SingleRow: expected exactly one row, got {len(table)}")
    return table[0]


def _table_remove_first_n(args: list[Any], ctx: _Ctx) -> Any:
    # Three documented forms: omitted -> remove exactly 1; a number -> remove
    # that many from the top; a condition -> remove rows from the top while
    # it holds, stopping at the first row that fails it (NOT a filter over
    # the whole table - Microsoft's own worked example proves the stop-at-
    # first-failure shape: {<=2,<=2,>2,>2} removes only the leading run).
    _arity("Table.RemoveFirstN", args, 1, 2)
    table = _require_table(args[0])
    if len(args) == 1:
        return table[1:]
    spec = args[1]
    if _is_invocable(spec):
        i = 0
        while i < len(table):
            keep_removing = ctx.invoke(spec, [table[i]], ctx)
            if not isinstance(keep_removing, bool):
                raise EvalError(
                    "Table.RemoveFirstN: condition must return a logical value"
                )
            if not keep_removing:
                break
            i += 1
        return table[i:]
    count = _require_int(spec)
    if count < 0:
        raise EvalError("Table.RemoveFirstN: count must not be negative")
    # A count past the end is not documented; mirrors this file's own
    # Table.FirstN/Table.LastN, which already clamp via plain slicing rather
    # than erroring.
    return table[count:]


def _table_remove_last_n(args: list[Any], ctx: _Ctx) -> Any:
    # Mirror of Table.RemoveFirstN, from the tail: a condition walks
    # backward from the last row and stops removing at the first (from-the-
    # end) row that fails it - confirmed by Microsoft's own worked example
    # (a leading-from-the-end run of `>=2` rows is removed, the first `<2`
    # row stops it).
    _arity("Table.RemoveLastN", args, 1, 2)
    table = _require_table(args[0])
    if len(args) == 1:
        return table[:-1]
    spec = args[1]
    if _is_invocable(spec):
        i = len(table)
        while i > 0:
            keep_removing = ctx.invoke(spec, [table[i - 1]], ctx)
            if not isinstance(keep_removing, bool):
                raise EvalError(
                    "Table.RemoveLastN: condition must return a logical value"
                )
            if not keep_removing:
                break
            i -= 1
        return table[:i]
    count = _require_int(spec)
    if count < 0:
        raise EvalError("Table.RemoveLastN: count must not be negative")
    return table[: len(table) - count]


def _table_remove_rows(args: list[Any], ctx: _Ctx) -> Any:
    # "Removes count of rows ... starting at offset. A default count of 1 is
    # used if count isn't provided." Out-of-range offset+count is not
    # documented; clamped via plain slicing (consistent with this file's own
    # Table.FirstN/LastN, which do not error on an over-long count either) -
    # only offset itself is bounds-checked, matching Table.Range's own
    # offset validation (_table_shape.py) for the sibling function that
    # shares this exact "start position into a table" shape.
    _arity("Table.RemoveRows", args, 2, 3)
    table = _require_table(args[0])
    offset = _require_int(args[1])
    if offset < 0:
        raise EvalError("Table.RemoveRows: offset must not be negative")
    if offset > len(table):
        raise EvalError("Table.RemoveRows: offset is out of range")
    count = _require_int(args[2]) if len(args) == 3 else 1
    if count < 0:
        raise EvalError("Table.RemoveRows: count must not be negative")
    return table[:offset] + table[offset + count :]


def _table_replace_rows(args: list[Any], ctx: _Ctx) -> Any:
    # All four parameters are required (no optional form, unlike
    # Table.RemoveRows). Splice: skip `offset` rows, delete the next `count`,
    # insert `rows` there - `rows`' length need not match `count` (Microsoft's
    # own worked example replaces 3 rows with 2, shrinking the table by one).
    _arity("Table.ReplaceRows", args, 4)
    table = _require_table(args[0])
    offset = _require_int(args[1])
    if offset < 0:
        raise EvalError("Table.ReplaceRows: offset must not be negative")
    if offset > len(table):
        raise EvalError("Table.ReplaceRows: offset is out of range")
    count = _require_int(args[2])
    if count < 0:
        raise EvalError("Table.ReplaceRows: count must not be negative")
    new_rows = _require_table(args[3])
    return table[:offset] + list(new_rows) + table[offset + count :]


def _table_distinct(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Distinct", args, 1, 2)
    table = _require_table(args[0])
    if len(args) == 2:
        names = _field_name_list(args[1])
        seen: list[tuple[Any, ...]] = []
        result = []
        for row in table:
            key = tuple(row.get(name) for name in names)
            if key not in seen:
                seen.append(key)
                result.append(row)
        return result
    result = []
    for row in table:
        if not any(_m_equal(row, other) for other in result):
            result.append(row)
    return result


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
BUILTINS: dict[str, Any] = {
    "Table.FromRecords": _table_from_records,
    "Table.ToRecords": _table_to_records,
    "Table.RowCount": _table_row_count,
    "Table.ColumnNames": _table_column_names,
    "Table.SelectRows": _table_select_rows,
    "Table.SelectColumns": _table_select_columns,
    "Table.RemoveColumns": _table_remove_columns,
    "Table.RenameColumns": _table_rename_columns,
    "Table.AddColumn": _table_add_column,
    "Table.TransformColumns": _table_transform_columns,
    "Table.Sort": _table_sort,
    "Table.FirstN": _table_first_n,
    "Table.LastN": _table_last_n,
    "Table.Distinct": _table_distinct,
    "Table.First": _table_first,
    "Table.FirstValue": _table_first_value,
    "Table.Last": _table_last,
    "Table.SingleRow": _table_single_row,
    "Table.RemoveFirstN": _table_remove_first_n,
    "Table.RemoveLastN": _table_remove_last_n,
    "Table.RemoveRows": _table_remove_rows,
    "Table.ReplaceRows": _table_replace_rows,
}
