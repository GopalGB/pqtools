"""``Table.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.

``Table.SelectRows``/``Table.AddColumn``/``Table.TransformColumns`` call
back into M lambdas via ``ctx.invoke`` rather than a module-level
``_invoke`` import - see the ``_Ctx.invoke`` docstring in ``evaluate.py``
for why.

``_ORDER_ENUM`` lives here (not in ``evaluate.py``) because ``Table.Sort``
is its only real consumer; ``evaluate.py``'s identifier resolution imports
it back from here to resolve the bare ``Order.Ascending``/``Order.Descending``
identifiers Power Query's UI emits.
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
    _arity("Table.AddColumn", args, 3)
    table = _require_table(args[0])
    name = _require_str(args[1])
    generator = args[2]
    if table and name in table[0]:
        raise EvalError(f"Table.AddColumn: column already exists: {name}")
    result = []
    for row in table:
        new_row = dict(row)
        new_row[name] = ctx.invoke(generator, [row], ctx)
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
_ORDER_ENUM = {"Order.Ascending": 0, "Order.Descending": 1}


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
