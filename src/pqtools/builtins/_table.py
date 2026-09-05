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

from ._list import _row_equation_criteria_predicate
from ._shared import (
    _MISSING_FIELD_ERROR,
    _MISSING_FIELD_IGNORE,
    EvalError,
    _arity,
    _column_selection,
    _field_name_list,
    _m_equal,
    _missing_field_mode,
    _require_int,
    _require_list,
    _require_record,
    _require_str,
    _require_table,
    _sort_criteria,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _table_from_records(args: list[Any], ctx: _Ctx) -> Any:
    """Table.FromRecords(records, optional columns, optional missingField).

    Only the first argument was accepted, so three of the page's four
    examples could not run - and neither could any other page's example that
    BUILDS its input with the two-argument form, which is why one missing
    optional argument accounted for six failures in the harvested corpus.
    """
    _arity("Table.FromRecords", args, 1, 3)
    records = [dict(_require_record(item)) for item in _require_list(args[0])]
    names = _column_selection(args[1] if len(args) >= 2 else None, "Table.FromRecords")
    if names is None:
        return records
    # "Using MissingField.Ignore in this parameter produces an error" -
    # verbatim, and structural: every row of a table shares one column set,
    # so a single row cannot quietly drop one.
    mode = _missing_field_mode(args[2] if len(args) == 3 else None)
    if mode == _MISSING_FIELD_IGNORE:
        raise EvalError(
            "Table.FromRecords: MissingField.Ignore is not valid here - a row "
            "cannot omit a column the table has. Use MissingField.UseNull."
        )
    rows: list[dict[str, Any]] = []
    for record in records:
        if mode == _MISSING_FIELD_ERROR:
            for name in names:
                if name not in record:
                    raise EvalError(
                        f"Table.FromRecords: a record is missing field {name!r} "
                        "(pass MissingField.UseNull to fill it with null)"
                    )
        rows.append({name: record.get(name) for name in names})
    return rows


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
    # missingField was missing, so Example 2 - which selects a column the
    # table does not have and asks for nulls - raised instead of running.
    _arity("Table.SelectColumns", args, 2, 3)
    names = _field_name_list(args[1])
    table = _require_table(args[0])
    mode = _missing_field_mode(args[2] if len(args) == 3 else None)
    known = set(table[0]) if table else set()
    if table:
        if mode == _MISSING_FIELD_ERROR:
            for name in names:
                if name not in known:
                    raise EvalError(f"Table.SelectColumns: no such column: {name}")
        elif mode == _MISSING_FIELD_IGNORE:
            names = [name for name in names if name in known]
    return [{name: row.get(name) for name in names} for row in table]


def _table_remove_columns(args: list[Any], ctx: _Ctx) -> Any:
    # "an error is raised unless the optional parameter missingField
    # specifies an alternative behavior (for example, MissingField.UseNull or
    # MissingField.Ignore)" - the page, verbatim. The parameter was absent, so
    # the escape hatch it names could not be reached. Ignore and UseNull are
    # the same instruction for a REMOVAL (there is no value left to null out).
    _arity("Table.RemoveColumns", args, 2, 3)
    names = _field_name_list(args[1])
    mode = _missing_field_mode(args[2] if len(args) == 3 else None)
    result = []
    for row in _require_table(args[0]):
        if mode == _MISSING_FIELD_ERROR:
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
    # missingField makes `Table.RenameColumns(t, {...}, MissingField.Ignore)`
    # work - the defensive form real queries use when an upstream source may
    # or may not carry a column.
    _arity("Table.RenameColumns", args, 2, 3)
    table = _require_table(args[0])
    pairs = _column_pairs(args[1], "Table.RenameColumns")
    mode = _missing_field_mode(args[2] if len(args) == 3 else None)
    added: list[str] = []
    mapping: dict[str, str] = {}
    for old, new in pairs:
        if not isinstance(new, str):
            raise EvalError("Table.RenameColumns: new column name must be text")
        if table and old not in table[0]:
            if mode == _MISSING_FIELD_ERROR:
                raise EvalError(f"Table.RenameColumns: no such column: {old}")
            if mode == _MISSING_FIELD_IGNORE:
                continue
            # MissingField.UseNull. The page names it as an accepted value but
            # shows no worked example of it on a RENAME, so this reads the
            # enum's own definition - "any missing fields are included as null
            # values" - literally: the absent column arrives, under its new
            # name, empty. test_table_optional_arguments.py says so out loud.
            added.append(new)
            continue
        mapping[old] = new
    if added:
        table = [{**row, **dict.fromkeys(added)} for row in table]
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


def _column_operations(value: Any, what: str) -> list[tuple[str, Any, Any]]:
    """`{column, transform}` or `{column, transform, newType}`, singly or listed.

    "The format of this parameter is either { column name, transformation }
    or { column name, transformation, new column type }" - verbatim. Only
    the two-element form was accepted, so every doc example that declares
    the resulting type (Date.From's and Date.FromText's among them) failed
    with "expected a {column, value} pair".

    The bare-entry-versus-list-of-entries ambiguity resolves the same way
    `_shared._sort_criteria` documents for Table.Sort: a column name is
    text, so an entry whose first element is text is one operation, and a
    list whose first element is itself a list is a list of operations.
    """
    if not isinstance(value, list):
        raise EvalError(f"{what}: expected a {{column, transformation}} entry")

    def as_entry(item: Any) -> tuple[str, Any, Any] | None:
        if (
            not isinstance(item, list)
            or not 2 <= len(item) <= 3
            or not isinstance(item[0], str)
        ):
            return None
        return (item[0], item[1], item[2] if len(item) == 3 else None)

    single = as_entry(value)
    if single is not None:
        return [single]
    entries: list[tuple[str, Any, Any]] = []
    for item in value:
        entry = as_entry(item)
        if entry is None:
            raise EvalError(
                f"{what}: each entry must be {{column, transformation}} or "
                "{column, transformation, newType}"
            )
        entries.append(entry)
    return entries


def _table_transform_columns(args: list[Any], ctx: _Ctx) -> Any:
    """Table.TransformColumns(table, ops, optional default, optional missing).

    Half the signature was absent: the three-element operation form, the
    defaultTransformation applied to every unlisted column, and missingField.
    """
    _arity("Table.TransformColumns", args, 2, 4)
    table = _require_table(args[0])
    operations = _column_operations(args[1], "Table.TransformColumns")
    default = args[2] if len(args) >= 3 else None
    mode = _missing_field_mode(args[3] if len(args) == 4 else None)

    known = set(table[0]) if table else set()
    planned: list[tuple[str, Any, Any]] = []
    for name, transform, declared in operations:
        if table and name not in known:
            if mode == _MISSING_FIELD_ERROR:
                raise EvalError(f"Table.TransformColumns: no such column: {name}")
            if mode == _MISSING_FIELD_IGNORE:
                continue
        planned.append((name, transform, declared))

    converters = {
        name: _declared_converter("Table.TransformColumns", declared)
        for name, _, declared in planned
        if declared is not None
    }
    listed = {name for name, _, _ in planned}

    result = []
    for row in table:
        new_row = dict(row)
        for name, transform, _ in planned:
            # MissingField.UseNull: a column the table never had is transformed
            # from null, exactly as the enum describes.
            value = ctx.invoke(transform, [new_row.get(name)], ctx)
            convert = converters.get(name)
            new_row[name] = convert(value) if convert is not None else value
        if default is not None:
            for name in list(new_row):
                if name not in listed:
                    new_row[name] = ctx.invoke(default, [new_row[name]], ctx)
        result.append(new_row)
    return result


def _declared_converter(what: str, declared: Any) -> Any:
    """The `new column type` slot, applied the way Table.AddColumn applies its own."""
    from ._type import _converter_for, _MType

    if not isinstance(declared, _MType):
        raise EvalError(
            f"{what}: expected a type value for the new column type, got "
            f"{_type_name(declared)}"
        )
    return _converter_for(declared)


# Order.Ascending / Order.Descending are M enum constants (0 and 1). Power Query's
# own UI emits `Table.Sort(t, {{"Col", Order.Ascending}})`, so without these the most
# common real-world sort is unusable.


def _table_sort(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Sort", args, 2)
    table = _require_table(args[0])
    # Shapes and their gotchas are documented on _sort_criteria. This used to
    # be a hand-rolled copy that rejected the bare `{"Col", Order.Descending}`
    # pair from the reference's own Example 2.
    keys = _sort_criteria(args[1], "Table.Sort")
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


def _row_condition(what: str, ctx: _Ctx, condition: Any, row: Any) -> bool:
    keep = ctx.invoke(condition, [row], ctx)
    if not isinstance(keep, bool):
        raise EvalError(f"{what}: condition must return a logical value")
    return keep


def _table_first_n(args: list[Any], ctx: _Ctx) -> Any:
    # The parameter is `countOrCondition`, not `count`: "if countOrCondition
    # is a condition, the rows that meet the condition will be returned until
    # a row does not meet the condition". The condition half was missing, so
    # the page's Example 2 raised "expected a number, got _Lambda". Note it
    # STOPS at the first row that fails - it is not Table.SelectRows.
    _arity("Table.FirstN", args, 2)
    table = _require_table(args[0])
    if _is_invocable(args[1]):
        kept = []
        for row in table:
            if not _row_condition("Table.FirstN", ctx, args[1], row):
                break
            kept.append(row)
        return kept
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Table.FirstN: count must not be negative")
    return table[:count]


def _table_last_n(args: list[Any], ctx: _Ctx) -> Any:
    # Mirror image: scan backwards from the end while the condition holds,
    # then return what was kept "in ascending position" (the page's words).
    _arity("Table.LastN", args, 2)
    table = _require_table(args[0])
    if _is_invocable(args[1]):
        kept = 0
        for row in reversed(table):
            if not _row_condition("Table.LastN", ctx, args[1], row):
                break
            kept += 1
        return table[len(table) - kept :] if kept else []
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
    # equationCriteria for tables is one concept with three shapes (a key
    # selector, a comparer, or a list of columns). This function understood
    # only the column list while Table.RemoveMatchingRows understood only the
    # functions, so each rejected the other's documented examples. Both now
    # go through one resolver - see _row_equation_criteria_predicate.
    _arity("Table.Distinct", args, 1, 2)
    table = _require_table(args[0])
    equal: Any = _m_equal
    if len(args) == 2 and args[1] is not None:
        equal = _row_equation_criteria_predicate(args[1], ctx, "Table.Distinct")
    result: list[Any] = []
    for row in table:
        if not any(equal(row, other) for other in result):
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
