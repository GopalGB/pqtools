"""``Table.Group``/``Table.*Join``/``Table.Expand*Column`` builtins - the
"pandas" half of the 0.5.0 expansion (PRD-0.5.0-builtins.md P1).

``Table.Group``/``Table.NestedJoin``/``Table.Join``/``Table.ExpandTableColumn``
call back into M lambdas (group aggregations) or need no callback at all
(joins/expand are pure data reshaping) - the callback path goes through
``ctx.invoke`` exactly like ``_table.py``'s ``Table.SelectRows``/``AddColumn``.

Enum resolution (``JoinKind.*``/``GroupKind.*``): this module reuses the same
resolution path every plain builtin goes through - ``evaluate.py``'s identifier
resolver looks the name up in ``BUILTINS``, and does not care whether the stored
value is callable. Registering
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

``Table.NestedJoin`` RightOuter/RightAnti/FullOuter - researched, not
guessed (2026-09-04). A prior implementer of this module left these three
kinds raising ``UnsupportedError`` because they could not verify, without
Power Query Desktop, which table's columns shape the result and what a
right-table-only row looks like. That gap is now closed from documentation
+ an independent empirical report, cross-checked across four sources:

1. Microsoft Learn, ``JoinKind.Type``
   (https://learn.microsoft.com/en-us/powerquery-m/joinkind-type):
   "A right outer join ensures that all rows of the second table appear in
   the result." / "A full outer join ensures that all rows of both tables
   appear in the result. Rows that did not have a match in the other table
   are joined with a default row containing null values for all of its
   columns." / "A right anti join returns all rows from the second table
   that do not have a match in the first table." (Written against
   ``Table.Join``, but the row-selection semantics - which rows survive -
   are the same join concept ``Table.NestedJoin`` implements; only the
   *shape* of the surviving rows differs between the two functions, which
   sources 2-4 below settle.)
2. Ben Gribaudo, "Deep Dive Into Joins (Part 1): Join vs. Nested Join"
   (https://bengribaudo.com/blog/2026/05/05/7714/deep-dive-into-joins-part-1-join-vs-nested-join),
   verbatim: "Table.NestedJoin does not, by itself, multiply rows. Each row
   from the left table that should be included in the join's output is
   included exactly one time ... Instead of multiplying rows, the
   joined-to rows are included in a nested table that is placed in a new
   column which is added to the table that's output. If, based on the join
   kind, rows from the right should be returned when they don't pair with
   a left row, an additional row is included in the output which has all
   columns from the left table set to null; only the row's nested join
   column will contain a value - a nested table containing the non-joining
   rows from the right." This is the load-bearing citation: NestedJoin
   NEVER reorients to become table2-shaped, for any kind, including the
   three that were refused. table1's columns are always the flat/outer
   part; the nested column always holds table2 data.
3. pqm.guide, "Table Joins" (https://pqm.guide/patterns/table-joins),
   worked ``Table.NestedJoin`` examples with ``table1=Sales``,
   ``table2=Customers``: for RightOuter, "every Customer row is kept, even
   with no Sales" and "After ... expand ..., columns from the side with no
   match will be null"; for RightAnti, "Expand to get Customer columns;
   Sales columns will all be null." Both confirm table1 (Sales) stays the
   null-able flat side and table2 (Customers) is what the nested column
   always carries, exactly matching source 2.
4. A real user's bug report on MrExcel
   (https://www.mrexcel.com/board/threads/power-query-unable-to-filter-out-nulls-after-table-nestedjoin-rightouter.893908/),
   independent empirical confirmation from an actual PQ session (not
   documentation prose): "A RightOuter join returns all the rows from the
   right table and the matching rows from the left table, and if there is
   no match, the left-side columns will contain null values" - table1
   (left) columns going null, not table2's, is exactly the "never
   reorients" shape from source 2.

One genuine ambiguity remains and is called out rather than silently
resolved: source 2 describes ONE additional row bundling ALL non-joining
right-table rows into a single nested table, as opposed to one additional
row per unmatched right-table row. No source shows the unexpanded
``Table.NestedJoin`` output's raw row count for this case to settle it
definitively. This module follows source 2's literal wording (bundle into
one row) because it is the only source specific enough to have an opinion.
Critically, the ambiguity is UNOBSERVABLE through the documented
``Table.NestedJoin`` + ``Table.ExpandTableColumn`` pipeline: an N-row
nested table expands to N output rows via ``Table.ExpandTableColumn``
regardless of how many top-level ``NestedJoin`` rows it was reached
through (see ``_table_expand_table_column`` below), so the two candidate
shapes are indistinguishable after the step every real usage actually
takes. That is what the NestedJoin-vs-Table.Join equivalence tests in
``tests/test_builtins_join.py`` verify - two independently-coded paths
(``Table.NestedJoin`` + ``Table.ExpandTableColumn`` vs. the already-correct
flat ``Table.Join``) agreeing on the observable, expanded result for
RightOuter/RightAnti/FullOuter, including duplicate-key and
unmatched-on-both-sides data.
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
    _require_list,
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

    if kind in (_JOIN_INNER, _JOIN_LEFT_OUTER, _JOIN_LEFT_ANTI):
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

    # RightOuter / RightAnti / FullOuter - see the module docstring
    # ("NestedJoin never reorients") for the citations behind this shape.
    # NestedJoin's output is ALWAYS table1-shaped (table1's own columns +
    # the nested column of table2 matches) no matter which JoinKind is
    # requested - it never flips to being table2-shaped. What changes per
    # kind is only WHICH rows appear and what backs the nested column:
    #   - table1 rows that matched table2: same as Inner (kept, nested =
    #     the matches) for every one of these three kinds.
    #   - table1 rows that did NOT match: kept with nested = [] for
    #     FullOuter (mirrors LeftOuter's own unmatched-row handling),
    #     dropped for RightOuter, never produced at all for RightAnti
    #     (RightAnti has no table1-driven rows whatsoever).
    #   - table2 rows that matched no table1 row at all: bundled into ONE
    #     extra row whose table1-side columns are null and whose nested
    #     column holds all of them together - Gribaudo's literal wording
    #     ("an additional row ... a nested table containing the
    #     non-joining rows from the right"). Only added when at least one
    #     such row exists (a spurious null row on a fully-matched dataset
    #     would disagree with Table.Join, which is this module's own
    #     equivalence check - see tests/test_builtins_join.py).
    # Bundled-into-one vs one-row-per-unmatched-right-row is unobservable
    # after Table.ExpandTableColumn either way (an N-row nested table
    # always expands to N output rows regardless of how many top-level
    # NestedJoin rows funnelled into it), which is exactly why the
    # NestedJoin-vs-Table.Join equivalence tests are real evidence here
    # rather than a restatement of this implementation.
    columns1 = list(table1[0].keys()) if table1 else []
    matched2_ids: set[int] = set()
    result = []
    if kind != _JOIN_RIGHT_ANTI:
        for row in table1:
            matches = _find_matches(row, keys1, table2, keys2, ctx, "Table.NestedJoin")
            for matched_row in matches:
                matched2_ids.add(id(matched_row))
            if kind == _JOIN_RIGHT_OUTER and not matches:
                continue
            new_row = dict(row)
            new_row[new_column] = matches
            result.append(new_row)
    else:
        # RightAnti contributes no table1-driven rows at all - still probe
        # every table1 row so unmatched table2 rows are known correctly.
        for row in table1:
            for matched_row in _find_matches(
                row, keys1, table2, keys2, ctx, "Table.NestedJoin"
            ):
                matched2_ids.add(id(matched_row))

    unmatched2 = [row2 for row2 in table2 if id(row2) not in matched2_ids]
    if unmatched2:
        null_row: dict[str, Any] = dict.fromkeys(columns1)
        null_row[new_column] = unmatched2
        result.append(null_row)
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


# --------------------------------------------------------------------------
# Table.ContainsAll / Table.ContainsAny / Table.IsDistinct /
# Table.PositionOfAny / Table.ReplaceMatchingRows / Table.AddRankColumn /
# Table.MaxN / Table.MinN / Table.AddJoinColumn / Table.AggregateTableColumn
# - the 0.10.0 gap-fill batch's set/rank/join half. Grounded against
# learn.microsoft.com/en-us/powerquery-m/<name-lowercased>, one page per
# function (RankKind.Type separately for Table.AddRankColumn's option).
# --------------------------------------------------------------------------


def _is_invocable(value: Any) -> bool:
    """True if ``ctx.invoke(value, ...)`` can call ``value`` as an M function.

    Duplicated in miniature from ``_table_shape.py``'s own ``_is_invocable``
    rather than imported - see that module's docstring and
    ``_parse_comparison_keys``'s docstring for why sibling ``_table*.py``
    files duplicate small helpers instead of cross-importing.
    """
    return callable(value) or (
        hasattr(value, "params") and hasattr(value, "body") and hasattr(value, "scope")
    )


def _row_match_predicate(
    criteria: Any,
) -> Any:
    """A ``(search_record, row) -> bool`` test for the Table.Contains*/
    Table.PositionOfAny/Table.ReplaceMatchingRows family.

    No ``equationCriteria``: subset match on the search record's OWN
    fields - the same convention this file's sibling ``_table_shape.py``
    already implements for ``Table.Contains``/``Table.PositionOf``
    (``all(_m_equal(row.get(k), v) for k, v in search.items())``),
    duplicated here for the same "keep modules self-contained" reason as
    ``_is_invocable`` above.

    Given ``equationCriteria``: Microsoft's own examples for
    ``Table.ContainsAll``/``Table.ContainsAny`` show ONLY a bare column
    name (``"CustomerID"``), narrowing the comparison to that one field
    rather than the whole search record. This accepts a name OR a list of
    names via the same ``_field_name_list`` this codebase already uses
    uniformly for every other "one or more column names" parameter
    (``Table.SelectColumns``, ``Table.Distinct``, ``Table.HasColumns``,
    ...) - a deliberate, consistent generalisation of the one documented
    shape, not an invented one, and pinned by a test naming it as such.
    """
    if criteria is None:

        def match_all_fields(search: dict[str, Any], row: dict[str, Any]) -> bool:
            return all(_m_equal(row.get(k), v) for k, v in search.items())

        return match_all_fields

    names = _field_name_list(criteria)

    def match_named_fields(search: dict[str, Any], row: dict[str, Any]) -> bool:
        return all(_m_equal(row.get(name), search.get(name)) for name in names)

    return match_named_fields


def _table_contains_all(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ContainsAll", args, 2, 3)
    table = _require_table(args[0])
    search_rows = [_require_record(r) for r in _require_list(args[1])]
    match = _row_match_predicate(args[2] if len(args) == 3 else None)
    return all(any(match(search, row) for row in table) for search in search_rows)


def _table_contains_any(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ContainsAny", args, 2, 3)
    table = _require_table(args[0])
    search_rows = [_require_record(r) for r in _require_list(args[1])]
    match = _row_match_predicate(args[2] if len(args) == 3 else None)
    return any(any(match(search, row) for row in table) for search in search_rows)


def _table_is_distinct(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.IsDistinct(table, comparisonCriteria?)`` - "If
    comparisonCriteria is not specified, all columns are tested" (verbatim
    from Microsoft's own page).

    Named-columns form mirrors this file's sibling ``_table.py``'s own
    ``Table.Distinct`` 2-argument form exactly, INCLUDING its use of plain
    Python tuple equality rather than ``_m_equal`` (duplicated rather than
    imported, per this module's established self-contained-file
    convention) - consistency with that sibling matters more here than
    picking a different equality rule for what is its boolean-negation
    counterpart. The no-criteria form mirrors ``Table.Distinct``'s own
    no-argument form, which does use ``_m_equal`` on the whole row.
    """
    _arity("Table.IsDistinct", args, 1, 2)
    table = _require_table(args[0])
    if len(args) == 2 and args[1] is not None:
        names = _field_name_list(args[1])
        seen: list[tuple[Any, ...]] = []
        for row in table:
            key = tuple(row.get(name) for name in names)
            if key in seen:
                return False
            seen.append(key)
        return True
    seen_rows: list[dict[str, Any]] = []
    for row in table:
        if any(_m_equal(row, other) for other in seen_rows):
            return False
        seen_rows.append(row)
    return True


def _table_position_of_any(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.PositionOfAny(table, rows, occurrence?, equationCriteria?)``.

    Occurrence handling mirrors this codebase's already-implemented
    ``List.PositionOfAny`` (``_list.py``) exactly - same
    ``Occurrence.First``/``Last``/``All`` values (0/1/2), same "-1 when
    absent" contract, verified against Microsoft's own two worked examples
    (default -> a single int; ``Occurrence.All`` -> the full list of
    positions).
    """
    _arity("Table.PositionOfAny", args, 2, 4)
    table = _require_table(args[0])
    search_rows = [_require_record(r) for r in _require_list(args[1])]
    occurrence = 0
    if len(args) >= 3 and args[2] is not None:
        occurrence = _require_int(args[2])
        if occurrence not in (0, 1, 2):
            raise UnsupportedError(
                "Table.PositionOfAny: occurrence must be Occurrence.First (0), "
                "Occurrence.Last (1), or Occurrence.All (2)"
            )
    match = _row_match_predicate(args[3] if len(args) == 4 else None)
    positions = [
        i
        for i, row in enumerate(table)
        if any(match(search, row) for search in search_rows)
    ]
    if occurrence == 2:
        return positions
    if not positions:
        return -1
    return positions[0] if occurrence == 0 else positions[-1]


def _row_replacement_pairs(value: Any, what: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """``{{old1, new1}, {old2, new2}, ...}`` - a list of ``{old, new}``
    record pairs, exactly Microsoft's own (only) worked example's shape.
    No bare single-pair shorthand is documented anywhere for this
    function (unlike e.g. ``Table.RenameColumns``'s), so none is accepted.
    """
    pairs_list = _require_list(value)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in pairs_list:
        if not (isinstance(item, list) and len(item) == 2):
            raise EvalError(f"{what}: expected a list of {{old, new}} record pairs")
        old, new = item
        pairs.append((_require_record(old), _require_record(new)))
    return pairs


def _table_replace_matching_rows(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.ReplaceMatchingRows(table, replacements, equationCriteria?)``.

    Whole-row replacement, ALL matching occurrences replaced (Microsoft's
    own worked example replaces both copies of a duplicated row) - not a
    merge/patch of individual fields. A table row matching more than one
    ``{old, new}`` spec is not addressed by any example; first-match-in-
    list-order wins here, a deliberate, tested choice for that unaddressed
    overlap rather than a verified rule.
    """
    _arity("Table.ReplaceMatchingRows", args, 2, 3)
    table = _require_table(args[0])
    pairs = _row_replacement_pairs(args[1], "Table.ReplaceMatchingRows")
    match = _row_match_predicate(args[2] if len(args) == 3 else None)
    result: list[dict[str, Any]] = []
    for row in table:
        replaced = row
        for old, new in pairs:
            if match(old, row):
                replaced = new
                break
        result.append(replaced)
    return result


def _is_order_value(value: Any) -> bool:
    return not isinstance(value, bool) and value in (0, 1)


def _parse_rank_criteria(spec: Any, what: str) -> list[tuple[str, bool]]:
    """comparisonCriteria for AddRankColumn/MaxN/MinN, as Table.Sort's OWN
    current Microsoft Learn page documents it: a bare column name, a bare
    ``{"Column", Order.Ascending|Descending}`` pair, or a list mixing plain
    names and such pairs.

    Deliberately NOT this codebase's existing ``_table_sort``/
    ``_parse_comparison_keys`` (``_table.py``/``_table_shape.py``): those
    predate Table.Sort's own bare-pair example and reject it outright - a
    real, pre-existing gap in code this task does not own, flagged
    separately rather than silently worked around by reusing it here. A
    bare pair is a 2-element list whose first element is a string and whose
    SECOND element is an Order.* value (0 or 1, not a bool) - a 2-element
    list of two plain column-name strings is instead "two ascending
    columns", disambiguated by that second-element type check. Verified
    against Table.Sort's 3 documented shapes plus Table.AddRankColumn's own
    worked example, which uses the bare-pair form directly.
    """

    def parse_entry(entry: Any) -> tuple[str, bool]:
        if isinstance(entry, str):
            return (entry, False)
        if (
            isinstance(entry, list)
            and len(entry) == 2
            and isinstance(entry[0], str)
            and _is_order_value(entry[1])
        ):
            return (entry[0], entry[1] == 1)
        raise UnsupportedError(
            f"{what}: comparisonCriteria entries must be a column name or "
            '{"Column", Order.Ascending}'
        )

    if isinstance(spec, str):
        return [(spec, False)]
    if not isinstance(spec, list):
        raise UnsupportedError(
            f"{what} with a {type(spec).__name__} comparisonCriteria"
        )
    if len(spec) == 2 and isinstance(spec[0], str) and _is_order_value(spec[1]):
        return [(spec[0], spec[1] == 1)]
    return [parse_entry(item) for item in spec]


# RankKind.Type - registered here rather than in the shared `_enums.py`
# because nothing outside this one function consumes it, exactly the
# precedent this file's own JoinKind.*/GroupKind.* enums already set (see
# the module docstring above). Values verified against Microsoft Learn's
# RankKind.Type page.
_RANK_COMPETITION = 0
_RANK_DENSE = 1
_RANK_ORDINAL = 2
_RANK_KIND_ENUM_BUILTINS: dict[str, Any] = {
    "RankKind.Competition": _RANK_COMPETITION,
    "RankKind.Dense": _RANK_DENSE,
    "RankKind.Ordinal": _RANK_ORDINAL,
}


def _table_add_rank_column(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.AddRankColumn(table, newColumnName, comparisonCriteria, options?)``.

    The output is REORDERED to rank order (best rank first) - easy to
    miss, but Microsoft's own worked example proves it: the input row
    order is Bob/Jim/Paul/Ringo, the output is Bob/Paul/Jim/Ringo (Paul,
    tied with Bob for rank 1, moves ahead of Jim). ``options.RankKind``
    defaults to ``RankKind.Competition`` when omitted - genuinely
    undocumented (the one worked example always passes it explicitly);
    value 0 is chosen as the "no option requested" default for the same
    reason Order.Ascending (also 0) is this codebase's default sort
    direction elsewhere - a named, tested choice, not a verified fact.
    """
    _arity("Table.AddRankColumn", args, 3, 4)
    table = _require_table(args[0])
    new_column = _require_str(args[1])
    keys = _parse_rank_criteria(args[2], "Table.AddRankColumn")
    for name, _ in keys:
        if table and name not in table[0]:
            raise EvalError(f"Table.AddRankColumn: no such column: {name}")
    if table and new_column in table[0]:
        raise EvalError(f"Table.AddRankColumn: column already exists: {new_column}")
    rank_kind = _RANK_COMPETITION
    if len(args) == 4 and args[3] is not None:
        options = dict(_require_record(args[3]))
        if "RankKind" in options:
            rank_kind = options.pop("RankKind")
            if rank_kind not in (_RANK_COMPETITION, _RANK_DENSE, _RANK_ORDINAL):
                raise UnsupportedError(
                    "Table.AddRankColumn: RankKind must be RankKind.Competition, "
                    "RankKind.Dense, or RankKind.Ordinal"
                )
        if options:
            raise UnsupportedError(
                f"Table.AddRankColumn: option(s) {sorted(options)}"
            )

    ranked = list(table)
    try:
        for name, descending in reversed(keys):
            ranked.sort(key=lambda row: row[name], reverse=descending)
    except TypeError as error:
        raise EvalError("Table.AddRankColumn: values are not comparable") from error

    def same_rank(a: dict[str, Any], b: dict[str, Any]) -> bool:
        return all(_m_equal(a[name], b[name]) for name, _ in keys)

    result: list[dict[str, Any]] = []
    rank = 0
    for position, row in enumerate(ranked):
        new_group = position == 0 or not same_rank(ranked[position - 1], row)
        if rank_kind == _RANK_ORDINAL:
            rank = position + 1
        elif new_group:
            rank = position + 1 if rank_kind == _RANK_COMPETITION else rank + 1
        new_row = dict(row)
        new_row[new_column] = rank
        result.append(new_row)
    return result


def _table_max_n_or_min_n(
    args: list[Any], ctx: _Ctx, want_max: bool, what: str
) -> Any:
    _arity(what, args, 3)
    table = _require_table(args[0])
    keys = _parse_rank_criteria(args[1], what)
    for name, _ in keys:
        if table and name not in table[0]:
            raise EvalError(f"{what}: no such column: {name}")
    ranked = list(table)
    try:
        for name, descending in reversed(keys):
            ranked.sort(key=lambda row: row[name], reverse=descending)
    except TypeError as error:
        raise EvalError(f"{what}: values are not comparable") from error
    if want_max:
        # Mirrors this codebase's own Table.Max (_table_shape.py): the
        # comparisonCriteria sort is always ascending-with-per-key-reverse-
        # flags; Table.Max takes the LAST row, Table.MaxN takes the last
        # `count` (or while-condition run) off that SAME ascending sort and
        # reverses it to present largest-first - exactly the order
        # Microsoft's own MaxN worked example shows, which directly
        # contradicts that page's "in ascending order" prose (a copy-paste
        # artifact off MinN's page, whose own example IS ascending). The
        # worked example is the stronger evidence and is what this follows.
        ranked = list(reversed(ranked))
    spec = args[2]
    if _is_invocable(spec):
        # "Once an item fails the condition, no further items are
        # considered" (verbatim) - a stop-at-first-failure walk over the
        # already best-first-ordered sequence, not a filter over the whole
        # table. Confirmed by Microsoft's own second example: the very
        # first (best) candidate fails, so the result is an empty table,
        # not the empty set of every row that happens to fail.
        result: list[dict[str, Any]] = []
        for row in ranked:
            keep = ctx.invoke(spec, [row], ctx)
            if not isinstance(keep, bool):
                raise EvalError(f"{what}: condition must return a logical value")
            if not keep:
                break
            result.append(row)
        return result
    count = _require_int(spec)
    if count < 0:
        raise EvalError(f"{what}: count must not be negative")
    return ranked[:count]


def _table_max_n(args: list[Any], ctx: _Ctx) -> Any:
    return _table_max_n_or_min_n(args, ctx, want_max=True, what="Table.MaxN")


def _table_min_n(args: list[Any], ctx: _Ctx) -> Any:
    return _table_max_n_or_min_n(args, ctx, want_max=False, what="Table.MinN")


def _table_add_join_column(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.AddJoinColumn(table1, key1, table2, key2, newColumnName)``.

    Microsoft's own page, verbatim: "This function behaves identically to
    Table.NestedJoin with joinKind set to JoinKind.LeftOuter." A direct,
    fully-specified equivalence to this file's own already-implemented
    ``Table.NestedJoin`` - not sugar with a twist, so it delegates rather
    than re-implementing (confirmed against the worked example: the new
    column holds a nested table of the FULL matching row(s), including
    columns beyond the joined-on key, exactly NestedJoin's own shape).
    """
    _arity("Table.AddJoinColumn", args, 5)
    return _table_nested_join([*args, _JOIN_LEFT_OUTER], ctx)


def _table_aggregate_table_column(args: list[Any], ctx: _Ctx) -> Any:
    """``Table.AggregateTableColumn(table, column, aggregations)``.

    ``aggregations`` is a list of ``{sourceColumnInNestedTable,
    aggregationFunction, newColumnName}`` triples, verified against
    Microsoft's one worked example (4 aggregations over one nested-table
    column). The nested-table column is dropped from the output, replaced
    by the new aggregate columns (which the example's own output places
    FIRST, ahead of the outer table's other, untouched columns - matched
    here exactly, not just approximated by dict equality).
    """
    _arity("Table.AggregateTableColumn", args, 3)
    table = _require_table(args[0])
    column = _require_str(args[1])
    agg_specs = _require_list(args[2])
    specs: list[tuple[str, Any, str]] = []
    seen_names: set[str] = set()
    for item in agg_specs:
        if not (
            isinstance(item, list)
            and len(item) == 3
            and isinstance(item[0], str)
            and isinstance(item[2], str)
        ):
            raise EvalError(
                "Table.AggregateTableColumn: aggregations must be "
                "{sourceColumn, function, newColumnName} triples"
            )
        source_column, function, new_name = item
        if new_name in seen_names:
            raise EvalError(
                f"Table.AggregateTableColumn: duplicate aggregate column: {new_name}"
            )
        seen_names.add(new_name)
        specs.append((source_column, function, new_name))
    result: list[dict[str, Any]] = []
    for row in table:
        if column not in row:
            raise EvalError(f"Table.AggregateTableColumn: no such column: {column}")
        nested = _require_table(row[column])
        remaining = {key: value for key, value in row.items() if key != column}
        for name in seen_names:
            if name in remaining:
                raise EvalError(
                    f"Table.AggregateTableColumn: column already exists: {name}"
                )
        new_row: dict[str, Any] = {}
        for source_column, function, new_name in specs:
            if nested and source_column not in nested[0]:
                raise EvalError(
                    "Table.AggregateTableColumn: no such column in nested "
                    f"table: {source_column}"
                )
            values = [nested_row.get(source_column) for nested_row in nested]
            new_row[new_name] = ctx.invoke(function, [values], ctx)
        new_row.update(remaining)
        result.append(new_row)
    return result


BUILTINS.update(
    {
        "Table.ContainsAll": _table_contains_all,
        "Table.ContainsAny": _table_contains_any,
        "Table.IsDistinct": _table_is_distinct,
        "Table.PositionOfAny": _table_position_of_any,
        "Table.ReplaceMatchingRows": _table_replace_matching_rows,
        "Table.AddRankColumn": _table_add_rank_column,
        "Table.MaxN": _table_max_n,
        "Table.MinN": _table_min_n,
        "Table.AddJoinColumn": _table_add_join_column,
        "Table.AggregateTableColumn": _table_aggregate_table_column,
        **_RANK_KIND_ENUM_BUILTINS,
    }
)
