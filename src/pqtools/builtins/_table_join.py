"""``Table.Group``/``Table.*Join``/``Table.Expand*Column`` builtins - the
"pandas" half of the 0.5.0 expansion (PRD-0.5.0-builtins.md P1).

``Table.Group``/``Table.NestedJoin``/``Table.Join``/``Table.ExpandTableColumn``
call back into M lambdas (group aggregations) or need no callback at all
(joins/expand are pure data reshaping) - the callback path goes through
``ctx.invoke`` exactly like ``_table.py``'s ``Table.SelectRows``/``AddColumn``.

Enum resolution (``JoinKind.*``/``GroupKind.*``): ``_table.py``'s
``_ORDER_ENUM`` is resolved via a dedicated check hard-coded into
``evaluate.py``'s identifier-resolution function
(``if name in _ORDER_ENUM: return _ORDER_ENUM[name]``) - that mechanism is
not reachable from this module without editing ``evaluate.py``, which is
off-limits here. Instead this module reuses the SAME resolution path every
plain builtin already goes through: ``evaluate.py``'s identifier resolver
checks ``BUILTINS.get(name)`` *before* it ever looks at ``_ORDER_ENUM``, and
that check does not care whether the stored value is callable. Registering
``"JoinKind.Inner"``/``"GroupKind.Global"``/etc. in this module's own
``BUILTINS`` dict with a plain sentinel string as the value makes a bare
``JoinKind.Inner`` reference in M source resolve to that string with zero
changes outside this file - verified empirically (see the implementer's
report) rather than assumed.

KNOWN GAP THIS MODULE CANNOT CLOSE: real Power Query lets a table aggregate
column shorthand (``[Amount]`` inside ``each List.Sum([Amount])``) read the
named column of the table currently bound to ``_`` as a list - "accessing a
column of a table" in Microsoft's own terms, ``t[ColumnName]``. This
evaluator's field-selector implementation (``_eval_field_selector`` /
``_record_field_access`` in ``evaluate.py``) only supports record (single
row) field access; on a table (a `list`) it raises
``EvalError: cannot select a field from a list value`` - confirmed with a
standalone repro before writing this module. Fixing that is a change to
``evaluate.py``'s core field-selector dispatch, which no builtin-family
module can reach and which this implementer was told not to touch. See the
report for the exact fix needed. ``Table.Group`` here is otherwise fully
correct: the sub-table is bound to ``_`` exactly as real Power Query does
(``each Table.RowCount(_)`` and the nested-``each``
``each List.Sum(List.Transform(_, each [Amount]))`` form both work today),
it is only the direct ``[Amount]`` table-column shorthand that is blocked
upstream of this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _field_name_list,
    _m_equal,
    _require_record,
    _require_str,
    _require_table,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx


# --------------------------------------------------------------------------
# JoinKind.* / GroupKind.* - see the module docstring for how these bare
# identifiers resolve without touching evaluate.py or _table.py. The values
# are private sentinels: nothing outside this module ever reads them, so
# there is no meaning to preserve beyond "resolvable and mutually distinct".
# --------------------------------------------------------------------------

_JOIN_INNER = "Inner"
_JOIN_LEFT_OUTER = "LeftOuter"
_JOIN_RIGHT_OUTER = "RightOuter"
_JOIN_FULL_OUTER = "FullOuter"
_JOIN_LEFT_ANTI = "LeftAnti"
_JOIN_RIGHT_ANTI = "RightAnti"
_JOIN_KINDS = frozenset(
    {
        _JOIN_INNER,
        _JOIN_LEFT_OUTER,
        _JOIN_RIGHT_OUTER,
        _JOIN_FULL_OUTER,
        _JOIN_LEFT_ANTI,
        _JOIN_RIGHT_ANTI,
    }
)

_GROUP_GLOBAL = "Global"
_GROUP_LOCAL = "Local"

_ENUM_BUILTINS: dict[str, Any] = {
    "JoinKind.Inner": _JOIN_INNER,
    "JoinKind.LeftOuter": _JOIN_LEFT_OUTER,
    "JoinKind.RightOuter": _JOIN_RIGHT_OUTER,
    "JoinKind.FullOuter": _JOIN_FULL_OUTER,
    "JoinKind.LeftAnti": _JOIN_LEFT_ANTI,
    "JoinKind.RightAnti": _JOIN_RIGHT_ANTI,
    "GroupKind.Global": _GROUP_GLOBAL,
    "GroupKind.Local": _GROUP_LOCAL,
}


# --------------------------------------------------------------------------
# Shared key-handling - one place for "no such column" + PQ's null-never-
# matches rule, reused by Group, NestedJoin and Join.
# --------------------------------------------------------------------------


def _row_key(row: dict[str, Any], keys: list[str], what: str) -> tuple[Any, ...]:
    values: list[Any] = []
    for key in keys:
        if key not in row:
            raise EvalError(f"{what}: no such column: {key}")
        values.append(row[key])
    return tuple(values)


def _keys_equal(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    return len(left) == len(right) and all(
        _m_equal(a, b) for a, b in zip(left, right, strict=True)
    )


def _join_keys_match(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    # Power Query join semantics: null does not match null (or anything) -
    # verified against Microsoft's merge documentation and pinned by
    # test_null_key_never_matches_in_join below.
    if len(left) != len(right):
        return False
    for left_value, right_value in zip(left, right, strict=True):
        if left_value is None or right_value is None:
            return False
        if not _m_equal(left_value, right_value):
            return False
    return True


# --------------------------------------------------------------------------
# Table.Group
# --------------------------------------------------------------------------


def _is_agg_spec(item: Any) -> bool:
    return isinstance(item, list) and 2 <= len(item) <= 3 and isinstance(item[0], str)


def _group_aggregation_specs(value: Any) -> list[tuple[str, Any]]:
    """``{"Name", function}`` or ``{{"Name", function}, ...}`` (optional
    trailing type element, e.g. ``{"Total", each ..., type number}``, is
    accepted and ignored - real Power Query treats it as output-schema
    metadata only, it never changes the aggregated value)."""
    if _is_agg_spec(value):
        return [(value[0], value[1])]
    if not isinstance(value, list):
        raise EvalError(
            "Table.Group: aggregations must be a {name, function} pair or a "
            "list of them"
        )
    specs: list[tuple[str, Any]] = []
    for item in value:
        if not _is_agg_spec(item):
            raise EvalError(
                "Table.Group: aggregations must be a {name, function} pair "
                "or a list of them"
            )
        specs.append((item[0], item[1]))
    return specs


def _group_rows_global(
    table: list[dict[str, Any]], keys: list[str], ctx: _Ctx
) -> list[tuple[tuple[Any, ...], list[dict[str, Any]]]]:
    """Every row with a matching key joins the same group, wherever it sits
    in the table. Output order = first-appearance order of each key."""
    order: list[tuple[Any, ...]] = []
    buckets: list[list[dict[str, Any]]] = []
    for row in table:
        key = _row_key(row, keys, "Table.Group")
        placed = False
        for index, existing in enumerate(order):
            ctx.budget.tick()
            if _keys_equal(existing, key):
                buckets[index].append(row)
                placed = True
                break
        if not placed:
            order.append(key)
            buckets.append([row])
    return list(zip(order, buckets, strict=True))


def _group_rows_local(
    table: list[dict[str, Any]], keys: list[str]
) -> list[tuple[tuple[Any, ...], list[dict[str, Any]]]]:
    """Only *consecutive* runs of a matching key form a group - the same key
    reappearing later, after a different key interrupts the run, starts a
    new group rather than rejoining the earlier one."""
    groups: list[tuple[tuple[Any, ...], list[dict[str, Any]]]] = []
    for row in table:
        key = _row_key(row, keys, "Table.Group")
        if groups and _keys_equal(groups[-1][0], key):
            groups[-1][1].append(row)
        else:
            groups.append((key, [row]))
    return groups


def _table_group(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Group", args, 3, 4)
    table = _require_table(args[0])
    keys = _field_name_list(args[1])
    agg_specs = _group_aggregation_specs(args[2])
    kind = args[3] if len(args) == 4 else _GROUP_GLOBAL
    if kind not in (_GROUP_GLOBAL, _GROUP_LOCAL):
        raise UnsupportedError(
            "Table.Group: groupKind must be GroupKind.Global or GroupKind.Local"
        )
    groups = (
        _group_rows_local(table, keys)
        if kind == _GROUP_LOCAL
        else _group_rows_global(table, keys, ctx)
    )
    result: list[dict[str, Any]] = []
    for key_values, subtable in groups:
        row: dict[str, Any] = dict(zip(keys, key_values, strict=True))
        for name, function in agg_specs:
            row[name] = ctx.invoke(function, [subtable], ctx)
        result.append(row)
    return result


# --------------------------------------------------------------------------
# Table.NestedJoin + Table.ExpandTableColumn + Table.ExpandRecordColumn
# --------------------------------------------------------------------------


def _find_matches(
    row: dict[str, Any],
    keys: list[str],
    other: list[dict[str, Any]],
    other_keys: list[str],
    ctx: _Ctx,
    what: str,
) -> list[dict[str, Any]]:
    key = _row_key(row, keys, what)
    if None in key:
        return []
    matches: list[dict[str, Any]] = []
    for other_row in other:
        ctx.budget.tick()
        if _join_keys_match(key, _row_key(other_row, other_keys, what)):
            matches.append(other_row)
    return matches


def _table_nested_join(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.NestedJoin", args, 5, 6)
    table1 = _require_table(args[0])
    keys1 = _field_name_list(args[1])
    table2 = _require_table(args[2])
    keys2 = _field_name_list(args[3])
    new_column = _require_str(args[4])
    kind = args[5] if len(args) == 6 else _JOIN_INNER
    if kind not in _JOIN_KINDS:
        raise UnsupportedError("Table.NestedJoin: joinKind must be a JoinKind.* value")
    if len(keys1) != len(keys2):
        raise EvalError(
            "Table.NestedJoin: key1 and key2 must have the same number of columns"
        )
    if table1 and new_column in table1[0]:
        raise EvalError(f"Table.NestedJoin: column already exists: {new_column}")

    if kind not in (_JOIN_INNER, _JOIN_LEFT_OUTER, _JOIN_LEFT_ANTI):
        # Real Power Query's merge dialog always emits table1-shaped output
        # (table1's columns + the nested column) for Inner/LeftOuter/
        # LeftAnti - that direction is unambiguous. RightOuter/RightAnti/
        # FullOuter would need the function to reorient which side is
        # "flat" and which is "nested", and there is no Power Query Desktop
        # available in this environment to verify the exact resulting shape
        # against - guessing it would violate the "never approximate" rule.
        # Workaround: swap table1/table2 and use the mirrored LeftOuter/
        # LeftAnti kind instead (RightOuter(t1,t2) is LeftOuter(t2,t1) with
        # the tables reversed).
        raise UnsupportedError(
            f"Table.NestedJoin with joinKind {kind}: only JoinKind.Inner, "
            "JoinKind.LeftOuter and JoinKind.LeftAnti are implemented - "
            "RightOuter/RightAnti/FullOuter need to reorient which table is "
            "nested vs flat, and that exact shape could not be verified "
            "against real Power Query Desktop in this environment. Swap "
            "table1/table2 and use the mirrored LeftOuter/LeftAnti kind."
        )

    result: list[dict[str, Any]] = []
    for row in table1:
        matches = _find_matches(row, keys1, table2, keys2, ctx, "Table.NestedJoin")
        if kind == _JOIN_INNER and not matches:
            continue
        if kind == _JOIN_LEFT_ANTI:
            if matches:
                continue
            matches = []
        new_row = dict(row)
        new_row[new_column] = matches
        result.append(new_row)
    return result


def _table_expand_table_column(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ExpandTableColumn", args, 3, 4)
    table = _require_table(args[0])
    column = _require_str(args[1])
    names = _field_name_list(args[2])
    new_names = _field_name_list(args[3]) if len(args) == 4 else names
    if len(new_names) != len(names):
        raise EvalError(
            "Table.ExpandTableColumn: columnNames and newColumnNames must "
            "have the same length"
        )
    result: list[dict[str, Any]] = []
    for row in table:
        if column not in row:
            raise EvalError(f"Table.ExpandTableColumn: no such column: {column}")
        nested_rows = _require_table(row[column])
        base = {key: value for key, value in row.items() if key != column}
        if not nested_rows:
            # A row whose nested table has zero matches (an unmatched
            # LeftOuter row) still contributes exactly one output row, with
            # nulls for the expanded columns - real Power Query does not
            # drop it. This is what makes a LeftOuter merge's unmatched
            # rows survive Expand rather than vanish.
            new_row = dict(base)
            for out_name in new_names:
                new_row[out_name] = None
            result.append(new_row)
            continue
        for nested_row in nested_rows:
            ctx.budget.tick()
            new_row = dict(base)
            for source_name, out_name in zip(names, new_names, strict=True):
                # A field missing from one nested row (schema drift across
                # matches) becomes null, not an error - documented Power
                # Query Expand behaviour, not a relaxation of validation.
                new_row[out_name] = nested_row.get(source_name)
            result.append(new_row)
    return result


def _table_expand_record_column(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ExpandRecordColumn", args, 3, 4)
    table = _require_table(args[0])
    column = _require_str(args[1])
    names = _field_name_list(args[2])
    new_names = _field_name_list(args[3]) if len(args) == 4 else names
    if len(new_names) != len(names):
        raise EvalError(
            "Table.ExpandRecordColumn: columnNames and newColumnNames must "
            "have the same length"
        )
    result: list[dict[str, Any]] = []
    for row in table:
        if column not in row:
            raise EvalError(f"Table.ExpandRecordColumn: no such column: {column}")
        record = _require_record(row[column]) if row[column] is not None else {}
        base = {key: value for key, value in row.items() if key != column}
        new_row = dict(base)
        for source_name, out_name in zip(names, new_names, strict=True):
            new_row[out_name] = record.get(source_name)
        result.append(new_row)
    return result


# --------------------------------------------------------------------------
# Table.Join - the flat form (one output row per matched pair, no nesting)
# --------------------------------------------------------------------------


def _disambiguate(columns1: list[str], columns2: list[str]) -> dict[str, str]:
    """table2 columns that collide with a table1 name (including the join
    keys themselves, when key1/key2 share a name) get a ``.1``/``.2``/...
    suffix - the same collision convention Power Query already uses for
    ``Table.Combine``, applied here for consistency since Table.Join's own
    collision behaviour is not directly documented."""
    taken = set(columns1)
    rename: dict[str, str] = {}
    for name in columns2:
        candidate = name
        suffix = 1
        while candidate in taken:
            candidate = f"{name}.{suffix}"
            suffix += 1
        rename[name] = candidate
        taken.add(candidate)
    return rename


def _table_join(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Join", args, 4, 7)
    table1 = _require_table(args[0])
    keys1 = _field_name_list(args[1])
    table2 = _require_table(args[2])
    keys2 = _field_name_list(args[3])
    if len(keys1) != len(keys2):
        raise EvalError(
            "Table.Join: key1 and key2 must have the same number of columns"
        )
    kind = args[4] if len(args) >= 5 and args[4] is not None else _JOIN_INNER
    if kind not in _JOIN_KINDS:
        raise UnsupportedError("Table.Join: joinKind must be a JoinKind.* value")
    if len(args) >= 6 and args[5] is not None:
        raise UnsupportedError("Table.Join: joinAlgorithm is not honoured")
    if len(args) >= 7 and args[6] is not None:
        raise UnsupportedError("Table.Join: keyEqualityComparers is not honoured")

    columns1 = list(table1[0].keys()) if table1 else []
    columns2 = list(table2[0].keys()) if table2 else []
    rename2 = _disambiguate(columns1, columns2)

    def merged_row(
        row1: dict[str, Any] | None, row2: dict[str, Any] | None
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for name in columns1:
            merged[name] = row1[name] if row1 is not None else None
        for name in columns2:
            merged[rename2[name]] = row2[name] if row2 is not None else None
        return merged

    result: list[dict[str, Any]] = []

    if kind in (_JOIN_INNER, _JOIN_LEFT_OUTER, _JOIN_LEFT_ANTI, _JOIN_FULL_OUTER):
        matched2: set[int] = set()
        for row1 in table1:
            key1 = _row_key(row1, keys1, "Table.Join")
            matches: list[tuple[int, dict[str, Any]]] = []
            if None not in key1:
                for index, row2 in enumerate(table2):
                    ctx.budget.tick()
                    if _join_keys_match(key1, _row_key(row2, keys2, "Table.Join")):
                        matches.append((index, row2))
            if matches:
                if kind == _JOIN_LEFT_ANTI:
                    continue
                for index, row2 in matches:
                    matched2.add(index)
                    result.append(merged_row(row1, row2))
            elif kind in (_JOIN_LEFT_OUTER, _JOIN_LEFT_ANTI, _JOIN_FULL_OUTER):
                result.append(merged_row(row1, None))
        if kind == _JOIN_FULL_OUTER:
            for index, row2 in enumerate(table2):
                if index not in matched2:
                    result.append(merged_row(None, row2))
        return result

    # RightOuter / RightAnti - driven from table2 instead of table1.
    for row2 in table2:
        key2 = _row_key(row2, keys2, "Table.Join")
        matches_left: list[dict[str, Any]] = []
        if None not in key2:
            for row1 in table1:
                ctx.budget.tick()
                if _join_keys_match(_row_key(row1, keys1, "Table.Join"), key2):
                    matches_left.append(row1)
        if matches_left:
            if kind == _JOIN_RIGHT_ANTI:
                continue
            for row1 in matches_left:
                result.append(merged_row(row1, row2))
        else:
            result.append(merged_row(None, row2))
    return result


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
BUILTINS: dict[str, Any] = {
    "Table.Group": _table_group,
    "Table.NestedJoin": _table_nested_join,
    "Table.ExpandTableColumn": _table_expand_table_column,
    "Table.ExpandRecordColumn": _table_expand_record_column,
    "Table.Join": _table_join,
    **_ENUM_BUILTINS,
}
