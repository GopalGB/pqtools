"""Tests for ``src/pqtools/builtins/_table_join.py`` - Table.Group,
Table.NestedJoin/ExpandTableColumn/ExpandRecordColumn, Table.Join, and the
JoinKind.*/GroupKind.* enum identifiers.

See PRD-0.5.0-builtins.md P1 ("the pandas part"). Scenarios 02 and 03 of
``tests/fixtures/realworld/`` are the hand-verified ground truth this module
targets; several tests below reproduce that exact data + expected output
directly (bypassing the CSV/Table.TransformColumnTypes steps another agent
owns) so Table.Group/NestedJoin/ExpandTableColumn are pinned against the
same numbers a human worked out independently, not just internally
self-consistent.

The one thing this file cannot pin green: real Power Query's table-column
shorthand (``each List.Sum([Amount])``, where ``[Amount]`` means "the
Amount column of the table bound to ``_``, as a list") is blocked by
``evaluate.py``'s field-selector implementation, which only supports record
field access, not table-column access - confirmed by a standalone repro
before writing ``_table_join.py`` (see that module's docstring). Every
correctness test below uses the equivalent, already-supported nested-``each``
form (``each List.Sum(List.Transform(_, each [Amount]))``) to prove
Table.Group's actual mechanics (sub-table bound to ``_``, GroupKind,
row/column order) are right independent of that upstream gap. One xfail
test documents the gap itself so it turns into a real regression check the
moment evaluate.py's field selector is fixed.
"""

from __future__ import annotations

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Shared fixture data - mirrors tests/fixtures/realworld/02_group_and_aggregate
# and .../03_merge_two_tables exactly (already "typed", since
# Table.TransformColumnTypes is a different agent's module and not under
# test here).
# --------------------------------------------------------------------------

_TRANSACTIONS = [
    {"TransactionID": 1, "Region": "East", "Category": "Books", "Amount": 50.00},
    {"TransactionID": 2, "Region": "West", "Category": "Electronics", "Amount": 200.00},
    {"TransactionID": 3, "Region": "East", "Category": "Clothing", "Amount": 75.00},
    {"TransactionID": 4, "Region": "North", "Category": "Books", "Amount": 30.00},
    {"TransactionID": 5, "Region": "West", "Category": "Books", "Amount": 120.00},
    {"TransactionID": 6, "Region": "East", "Category": "Electronics", "Amount": 300.00},
    {"TransactionID": 7, "Region": "North", "Category": "Clothing", "Amount": 45.00},
    {"TransactionID": 8, "Region": "West", "Category": "Clothing", "Amount": 60.00},
    {"TransactionID": 9, "Region": "East", "Category": "Books", "Amount": 25.00},
    {
        "TransactionID": 10,
        "Region": "North",
        "Category": "Electronics",
        "Amount": 150.00,
    },
]

_GROUP_QUERY = (
    'Table.Group(Source, {"Region"}, '
    '{{"TotalSales", each List.Sum(List.Transform(_, each [Amount]))}, '
    '{"AverageSale", each List.Average(List.Transform(_, each [Amount]))}, '
    '{"TransactionCount", each Table.RowCount(_)}, '
    '{"MaxSale", each List.Max(List.Transform(_, each [Amount]))}})'
)

_EXPECTED_GROUPED = [
    {
        "Region": "East",
        "TotalSales": 450.0,
        "AverageSale": 112.5,
        "TransactionCount": 4,
        "MaxSale": 300.0,
    },
    {
        "Region": "West",
        "TotalSales": 380.0,
        "AverageSale": 126.66666666666667,
        "TransactionCount": 3,
        "MaxSale": 200.0,
    },
    {
        "Region": "North",
        "TotalSales": 225.0,
        "AverageSale": 75.0,
        "TransactionCount": 3,
        "MaxSale": 150.0,
    },
]

_ORDERS = [
    {"OrderID": 5001, "CustomerID": 1, "Amount": 120.00},
    {"OrderID": 5002, "CustomerID": 2, "Amount": 340.50},
    {"OrderID": 5003, "CustomerID": 1, "Amount": 75.25},
    {"OrderID": 5004, "CustomerID": 3, "Amount": 210.00},
    {"OrderID": 5005, "CustomerID": 99, "Amount": 500.00},
    {"OrderID": 5006, "CustomerID": 2, "Amount": 60.00},
]

_CUSTOMERS = [
    {"CustomerID": 1, "CustomerName": "Ava Thompson", "Tier": "Gold"},
    {"CustomerID": 2, "CustomerName": "Ben Okafor", "Tier": "Silver"},
    {"CustomerID": 3, "CustomerName": "Chloe Martin", "Tier": "Gold"},
    {"CustomerID": 4, "CustomerName": "David Wu", "Tier": "Bronze"},
]

_MERGE_QUERY = (
    "Table.ExpandTableColumn("
    'Table.NestedJoin(Source, {"CustomerID"}, CustomersSource, {"CustomerID"}, '
    '"CustomerData", JoinKind.LeftOuter), '
    '"CustomerData", {"CustomerName", "Tier"}, {"CustomerName", "Tier"})'
)

_EXPECTED_MERGED = [
    {
        "OrderID": 5001,
        "CustomerID": 1,
        "Amount": 120.0,
        "CustomerName": "Ava Thompson",
        "Tier": "Gold",
    },
    {
        "OrderID": 5002,
        "CustomerID": 2,
        "Amount": 340.5,
        "CustomerName": "Ben Okafor",
        "Tier": "Silver",
    },
    {
        "OrderID": 5003,
        "CustomerID": 1,
        "Amount": 75.25,
        "CustomerName": "Ava Thompson",
        "Tier": "Gold",
    },
    {
        "OrderID": 5004,
        "CustomerID": 3,
        "Amount": 210.0,
        "CustomerName": "Chloe Martin",
        "Tier": "Gold",
    },
    {
        "OrderID": 5005,
        "CustomerID": 99,
        "Amount": 500.0,
        "CustomerName": None,
        "Tier": None,
    },
    {
        "OrderID": 5006,
        "CustomerID": 2,
        "Amount": 60.0,
        "CustomerName": "Ben Okafor",
        "Tier": "Silver",
    },
]


# --------------------------------------------------------------------------
# Table.Group - pinned against the hand-verified scenario 02 numbers
# --------------------------------------------------------------------------


def test_group_matches_realworld_scenario_02():
    result = evaluate(_GROUP_QUERY, bindings={"Source": _TRANSACTIONS})
    assert result == _EXPECTED_GROUPED


def test_group_key_as_bare_string_matches_list_form():
    query_bare_key = _GROUP_QUERY.replace('{"Region"}', '"Region"')
    result = evaluate(query_bare_key, bindings={"Source": _TRANSACTIONS})
    assert result == _EXPECTED_GROUPED


def test_group_output_column_order_is_keys_then_aggregations_in_order():
    result = evaluate(_GROUP_QUERY, bindings={"Source": _TRANSACTIONS})
    assert list(result[0].keys()) == [
        "Region",
        "TotalSales",
        "AverageSale",
        "TransactionCount",
        "MaxSale",
    ]


def test_group_subtable_bound_to_underscore_rowcount_form():
    # `each Table.RowCount(_)` - the sub-table itself as `_`, no field
    # selector involved at all.
    query = 'Table.Group(Source, "k", {{"n", each Table.RowCount(_)}})'
    rows = [{"k": "a"}, {"k": "a"}, {"k": "b"}]
    assert evaluate(query, bindings={"Source": rows}) == [
        {"k": "a", "n": 2},
        {"k": "b", "n": 1},
    ]


def test_group_global_default_row_order_is_first_appearance():
    # Rows are B, A, B, A - Global must still bucket all A's together and
    # all B's together, ordered by each key's FIRST appearance (B, then A).
    rows = [
        {"k": "B", "v": 1},
        {"k": "A", "v": 2},
        {"k": "B", "v": 3},
        {"k": "A", "v": 4},
    ]
    query = (
        'Table.Group(Source, "k", '
        '{{"total", each List.Sum(List.Transform(_, each [v]))}})'
    )
    assert evaluate(query, bindings={"Source": rows}) == [
        {"k": "B", "total": 4},
        {"k": "A", "total": 6},
    ]


def test_group_kind_local_only_groups_consecutive_runs():
    # Same B/A/B/A rows: GroupKind.Local must NOT merge the two B-runs or
    # the two A-runs - each contiguous run is its own group, in table order.
    rows = [
        {"k": "B", "v": 1},
        {"k": "A", "v": 2},
        {"k": "B", "v": 3},
        {"k": "A", "v": 4},
    ]
    query = (
        'Table.Group(Source, "k", '
        '{{"total", each List.Sum(List.Transform(_, each [v]))}}, GroupKind.Local)'
    )
    assert evaluate(query, bindings={"Source": rows}) == [
        {"k": "B", "total": 1},
        {"k": "A", "total": 2},
        {"k": "B", "total": 3},
        {"k": "A", "total": 4},
    ]


def test_group_kind_local_merges_a_genuine_consecutive_run():
    rows = [{"k": "A", "v": 1}, {"k": "A", "v": 2}, {"k": "B", "v": 3}]
    query = (
        'Table.Group(Source, "k", '
        '{{"total", each List.Sum(List.Transform(_, each [v]))}}, GroupKind.Local)'
    )
    assert evaluate(query, bindings={"Source": rows}) == [
        {"k": "A", "total": 3},
        {"k": "B", "total": 3},
    ]


def test_group_multi_column_keys():
    rows = [
        {"a": 1, "b": "x", "v": 10},
        {"a": 1, "b": "y", "v": 20},
        {"a": 1, "b": "x", "v": 30},
    ]
    query = (
        'Table.Group(Source, {"a", "b"}, '
        '{{"total", each List.Sum(List.Transform(_, each [v]))}})'
    )
    assert evaluate(query, bindings={"Source": rows}) == [
        {"a": 1, "b": "x", "total": 40},
        {"a": 1, "b": "y", "total": 20},
    ]


def test_group_null_keys_group_together():
    # Group-by is not a join: unlike Table.Join's null-never-matches rule,
    # rows with a null key value DO belong to the same group as each other
    # (standard SQL-style GROUP BY semantics).
    rows = [{"k": None, "v": 1}, {"k": None, "v": 2}, {"k": "a", "v": 3}]
    query = (
        'Table.Group(Source, "k", '
        '{{"total", each List.Sum(List.Transform(_, each [v]))}})'
    )
    assert evaluate(query, bindings={"Source": rows}) == [
        {"k": None, "total": 3},
        {"k": "a", "total": 3},
    ]


def test_group_empty_aggregations_returns_distinct_keys():
    rows = [{"k": "a"}, {"k": "a"}, {"k": "b"}]
    assert evaluate('Table.Group(Source, "k", {})', bindings={"Source": rows}) == [
        {"k": "a"},
        {"k": "b"},
    ]


def test_group_aggregation_type_annotation_is_accepted_and_ignored():
    # Real Power Query's 3-element aggregation form ({name, function, type})
    # only affects the inferred output schema's type - not the value - so
    # this evaluator (list-of-dicts, no column typing) correctly accepts and
    # ignores the third element rather than erroring on it. `type number`
    # itself is not yet evaluable (P0 type system, a different agent's
    # module) so this uses the other spelling PQ's UI also emits:
    # `Int64.Type` resolves today because it is a plain identifier the
    # evaluator can already look up as a builtin-registered name once the
    # type-system agent lands it; until then this test documents intent via
    # a value the evaluator CAN already resolve.
    rows = [{"k": "a", "v": 1}, {"k": "a", "v": 2}]
    query = (
        'Table.Group(Source, "k", '
        '{{"total", each List.Sum(List.Transform(_, each [v])), 42}})'
    )
    assert evaluate(query, bindings={"Source": rows}) == [{"k": "a", "total": 3}]


def test_group_bad_group_kind_is_unsupported():
    rows = [{"k": "a"}]
    query = 'Table.Group(Source, "k", {{"n", each Table.RowCount(_)}}, "NotAKind")'
    with pytest.raises(UnsupportedError, match="GroupKind"):
        evaluate(query, bindings={"Source": rows})


def test_group_missing_key_column_errors():
    rows = [{"k": "a"}]
    with pytest.raises(EvalError, match="no such column: missing"):
        evaluate(
            'Table.Group(Source, "missing", {{"n", each Table.RowCount(_)}})',
            bindings={"Source": rows},
        )


def test_group_wrong_arity_is_unsupported():
    with pytest.raises(UnsupportedError, match="Table.Group"):
        evaluate("Table.Group(Source)", bindings={"Source": []})


def test_group_direct_column_shorthand_in_aggregation():
    rows = [{"Region": "East", "Amount": 10}, {"Region": "East", "Amount": 20}]
    query = 'Table.Group(Source, "Region", {{"Total", each List.Sum([Amount])}})'
    assert evaluate(query, bindings={"Source": rows}) == [
        {"Region": "East", "Total": 30}
    ]


# --------------------------------------------------------------------------
# Table.NestedJoin + Table.ExpandTableColumn - pinned against scenario 03
# --------------------------------------------------------------------------


def test_nested_join_and_expand_matches_realworld_scenario_03():
    result = evaluate(
        _MERGE_QUERY,
        bindings={"Source": _ORDERS, "CustomersSource": _CUSTOMERS},
    )
    assert result == _EXPECTED_MERGED


def test_nested_join_left_outer_unmatched_row_survives_with_nulls():
    # CustomerID 99 has no match in _CUSTOMERS - it must still appear, with
    # null CustomerName/Tier, not be silently dropped.
    result = evaluate(
        _MERGE_QUERY,
        bindings={"Source": _ORDERS, "CustomersSource": _CUSTOMERS},
    )
    unmatched = next(row for row in result if row["OrderID"] == 5005)
    assert unmatched["CustomerName"] is None
    assert unmatched["Tier"] is None


def test_nested_join_duplicate_keys_multiply_rows_on_expand():
    # One left row whose key matches THREE right rows must expand to THREE
    # output rows, each carrying a different match - this is real join
    # semantics (a 1:many relationship), not a bug to be collapsed away.
    left = [{"id": 1, "tag": "only"}]
    right = [
        {"id": 1, "note": "first"},
        {"id": 1, "note": "second"},
        {"id": 1, "note": "third"},
    ]
    query = (
        "Table.ExpandTableColumn("
        'Table.NestedJoin(L, "id", R, "id", "matches", JoinKind.Inner), '
        '"matches", {"note"})'
    )
    result = evaluate(query, bindings={"L": left, "R": right})
    assert len(result) == 3
    assert {row["note"] for row in result} == {"first", "second", "third"}
    assert all(row["id"] == 1 and row["tag"] == "only" for row in result)


def test_nested_join_inner_drops_unmatched_left_rows():
    left = [{"id": 1}, {"id": 2}]
    right = [{"id": 1, "v": "x"}]
    query = 'Table.NestedJoin(L, "id", R, "id", "m", JoinKind.Inner)'
    result = evaluate(query, bindings={"L": left, "R": right})
    assert [row["id"] for row in result] == [1]
    assert result[0]["m"] == [{"id": 1, "v": "x"}]


def test_nested_join_left_anti_keeps_only_unmatched():
    left = [{"id": 1}, {"id": 2}]
    right = [{"id": 1, "v": "x"}]
    query = 'Table.NestedJoin(L, "id", R, "id", "m", JoinKind.LeftAnti)'
    result = evaluate(query, bindings={"L": left, "R": right})
    assert [row["id"] for row in result] == [2]
    assert result[0]["m"] == []


def test_nested_join_default_kind_is_inner():
    left = [{"id": 1}, {"id": 2}]
    right = [{"id": 1, "v": "x"}]
    result = evaluate(
        'Table.NestedJoin(L, "id", R, "id", "m")', bindings={"L": left, "R": right}
    )
    assert [row["id"] for row in result] == [1]


@pytest.mark.parametrize(
    "kind", ["JoinKind.RightOuter", "JoinKind.RightAnti", "JoinKind.FullOuter"]
)
def test_nested_join_right_and_full_kinds_are_refused_not_guessed(kind):
    left = [{"id": 1}]
    right = [{"id": 1, "v": "x"}]
    query = f'Table.NestedJoin(L, "id", R, "id", "m", {kind})'
    with pytest.raises(UnsupportedError, match="NestedJoin"):
        evaluate(query, bindings={"L": left, "R": right})


def test_expand_table_column_null_cell_errors_not_silently_treated_as_empty():
    rows = [{"id": 1, "nested": None}]
    with pytest.raises(EvalError):
        evaluate(
            'Table.ExpandTableColumn(Source, "nested", {"x"})',
            bindings={"Source": rows},
        )


def test_expand_table_column_rename():
    rows = [{"id": 1, "nested": [{"a": 10}]}]
    result = evaluate(
        'Table.ExpandTableColumn(Source, "nested", {"a"}, {"aRenamed"})',
        bindings={"Source": rows},
    )
    assert result == [{"id": 1, "aRenamed": 10}]


def test_expand_table_column_missing_field_in_one_match_becomes_null():
    # Schema drift across matches (one matched row lacks the requested
    # field) becomes null, per documented Power Query Expand behaviour -
    # not an error.
    rows = [{"id": 1, "nested": [{"a": 1}, {}]}]
    result = evaluate(
        'Table.ExpandTableColumn(Source, "nested", {"a"})',
        bindings={"Source": rows},
    )
    assert result == [{"id": 1, "a": 1}, {"id": 1, "a": None}]


def test_expand_record_column_basic_and_missing_field_is_null():
    rows = [
        {"id": 1, "info": {"name": "Ava", "tier": "Gold"}},
        {"id": 2, "info": {"name": "Ben"}},
    ]
    result = evaluate(
        'Table.ExpandRecordColumn(Source, "info", {"name", "tier"})',
        bindings={"Source": rows},
    )
    assert result == [
        {"id": 1, "name": "Ava", "tier": "Gold"},
        {"id": 2, "name": "Ben", "tier": None},
    ]


def test_nested_join_key_count_mismatch_errors():
    with pytest.raises(EvalError, match="same number of columns"):
        evaluate(
            'Table.NestedJoin(L, {"a", "b"}, R, "a", "m", JoinKind.Inner)',
            bindings={"L": [{"a": 1, "b": 2}], "R": [{"a": 1}]},
        )


def test_nested_join_new_column_already_exists_errors():
    with pytest.raises(EvalError, match="already exists"):
        evaluate(
            'Table.NestedJoin(L, "id", R, "id", "id", JoinKind.Inner)',
            bindings={"L": [{"id": 1}], "R": [{"id": 1}]},
        )


# --------------------------------------------------------------------------
# null-key handling - pinned per PRD's explicit correctness rule: null does
# NOT match null in a join (unlike Table.Group's grouping semantics above).
# --------------------------------------------------------------------------


def test_null_key_never_matches_in_join():
    left = [{"id": None, "tag": "left-null"}]
    right = [{"id": None, "tag": "right-null"}]
    result = evaluate(
        'Table.NestedJoin(L, "id", R, "id", "m", JoinKind.Inner)',
        bindings={"L": left, "R": right},
    )
    assert result == []  # no match, even though both keys are null

    left_outer = evaluate(
        'Table.NestedJoin(L, "id", R, "id", "m", JoinKind.LeftOuter)',
        bindings={"L": left, "R": right},
    )
    assert left_outer == [{"id": None, "tag": "left-null", "m": []}]


# --------------------------------------------------------------------------
# Table.Join - the flat form
# --------------------------------------------------------------------------


def test_join_inner_basic_and_duplicate_keys_multiply_rows():
    left = [{"id": 1, "a": "x"}, {"id": 2, "a": "y"}]
    right = [{"id": 1, "b": "p"}, {"id": 1, "b": "q"}, {"id": 3, "b": "r"}]
    result = evaluate(
        'Table.Join(L, "id", R, "id", JoinKind.Inner)',
        bindings={"L": left, "R": right},
    )
    assert len(result) == 2  # id=2 and id=3 rows have no counterpart
    assert {row["b"] for row in result} == {"p", "q"}
    assert all(row["id"] == 1 and row["a"] == "x" for row in result)


def test_join_left_outer_unmatched_row_gets_nulls_not_dropped():
    left = [{"id": 1, "a": "x"}, {"id": 2, "a": "y"}]
    right = [{"id": 1, "b": "p"}]
    result = evaluate(
        'Table.Join(L, "id", R, "id", JoinKind.LeftOuter)',
        bindings={"L": left, "R": right},
    )
    assert len(result) == 2
    unmatched = next(row for row in result if row["id"] == 2)
    assert unmatched["a"] == "y"
    assert unmatched["b"] is None


def test_join_right_outer_unmatched_right_row_gets_nulls():
    left = [{"id": 1, "a": "x"}]
    right = [{"id": 1, "b": "p"}, {"id": 2, "b": "q"}]
    result = evaluate(
        'Table.Join(L, "id", R, "id", JoinKind.RightOuter)',
        bindings={"L": left, "R": right},
    )
    assert len(result) == 2
    unmatched = next(row for row in result if row["b"] == "q")
    assert unmatched["a"] is None
    assert unmatched["id"] is None  # table1's own "id" column, not table2's


def test_join_full_outer_has_both_unmatched_sides():
    left = [{"id": 1, "a": "x"}, {"id": 2, "a": "y"}]
    right = [{"id": 1, "b": "p"}, {"id": 3, "b": "q"}]
    result = evaluate(
        'Table.Join(L, "id", R, "id", JoinKind.FullOuter)',
        bindings={"L": left, "R": right},
    )
    assert len(result) == 3
    by_shape = sorted(((row["a"] or ""), (row["b"] or "")) for row in result)
    assert by_shape == [("", "q"), ("x", "p"), ("y", "")]


def test_join_left_anti_and_right_anti():
    # Different key column names on each side (id vs rid) so this test
    # isolates anti-join row selection without also exercising the
    # same-name-key disambiguation behaviour (that is its own pinned test:
    # test_join_composite_keys_with_same_column_names_get_disambiguated).
    left = [{"id": 1}, {"id": 2}]
    right = [{"rid": 1}, {"rid": 3}]
    left_anti = evaluate(
        'Table.Join(L, "id", R, "rid", JoinKind.LeftAnti)',
        bindings={"L": left, "R": right},
    )
    assert [row["id"] for row in left_anti] == [2]
    right_anti = evaluate(
        'Table.Join(L, "id", R, "rid", JoinKind.RightAnti)',
        bindings={"L": left, "R": right},
    )
    assert [row["rid"] for row in right_anti] == [3]


def test_join_default_kind_is_inner():
    left = [{"id": 1}, {"id": 2}]
    right = [{"id": 1, "v": "x"}]
    result = evaluate('Table.Join(L, "id", R, "id")', bindings={"L": left, "R": right})
    assert [row["id"] for row in result] == [1]


def test_join_colliding_column_names_get_disambiguated():
    # Key columns use different names (id1/id2) so the only collision this
    # test exercises is the non-key "name" column on both sides.
    left = [{"id1": 1, "name": "left-name"}]
    right = [{"id2": 1, "name": "right-name"}]
    result = evaluate(
        'Table.Join(L, "id1", R, "id2", JoinKind.Inner)',
        bindings={"L": left, "R": right},
    )
    assert result == [{"id1": 1, "name": "left-name", "id2": 1, "name.1": "right-name"}]


def test_join_composite_keys():
    left = [{"a": 1, "b": "x", "v": "left"}]
    right = [{"ra": 1, "rb": "x", "w": "right"}, {"ra": 1, "rb": "y", "w": "nomatch"}]
    result = evaluate(
        'Table.Join(L, {"a", "b"}, R, {"ra", "rb"}, JoinKind.Inner)',
        bindings={"L": left, "R": right},
    )
    assert result == [{"a": 1, "b": "x", "v": "left", "ra": 1, "rb": "x", "w": "right"}]


def test_join_composite_keys_with_same_column_names_get_disambiguated():
    # When key1 and key2 literally share a name (the common real-world
    # case), Table.Join is not documented as unifying them into one output
    # column - this module applies the same ".1" suffix convention it uses
    # for any other name collision (see _disambiguate's docstring), so
    # table1's key column wins the bare name and table2's is renamed. This
    # test pins that deliberate, documented choice.
    left = [{"id": 1, "v": "left"}]
    right = [{"id": 1, "w": "right"}]
    result = evaluate(
        'Table.Join(L, "id", R, "id", JoinKind.Inner)',
        bindings={"L": left, "R": right},
    )
    assert result == [{"id": 1, "v": "left", "id.1": 1, "w": "right"}]


def test_join_key_count_mismatch_errors():
    with pytest.raises(EvalError, match="same number of columns"):
        evaluate(
            'Table.Join(L, {"a", "b"}, R, "a", JoinKind.Inner)',
            bindings={"L": [{"a": 1, "b": 2}], "R": [{"a": 1}]},
        )


def test_join_bad_kind_is_unsupported():
    with pytest.raises(UnsupportedError, match="JoinKind"):
        evaluate(
            'Table.Join(L, "id", R, "id", "NotAKind")',
            bindings={"L": [{"id": 1}], "R": [{"id": 1}]},
        )


def test_join_algorithm_argument_is_refused_not_ignored():
    with pytest.raises(UnsupportedError, match="joinAlgorithm"):
        evaluate(
            'Table.Join(L, "id", R, "id", JoinKind.Inner, 0)',
            bindings={"L": [{"id": 1}], "R": [{"id": 1}]},
        )


def test_join_key_equality_comparers_argument_is_refused_not_ignored():
    with pytest.raises(UnsupportedError, match="keyEqualityComparers"):
        evaluate(
            'Table.Join(L, "id", R, "id", JoinKind.Inner, null, {1})',
            bindings={"L": [{"id": 1}], "R": [{"id": 1}]},
        )


def test_join_wrong_arity_is_unsupported():
    with pytest.raises(UnsupportedError, match="Table.Join"):
        evaluate('Table.Join(L, "id", R)', bindings={"L": [], "R": []})


def test_join_respects_max_steps_budget_on_large_input():
    left = [{"id": i} for i in range(50)]
    right = [{"id": i} for i in range(50)]
    with pytest.raises(EvalError, match="max_steps"):
        evaluate(
            'Table.Join(L, "id", R, "id", JoinKind.Inner)',
            bindings={"L": left, "R": right},
            max_steps=20,
        )


# --------------------------------------------------------------------------
# JoinKind.* / GroupKind.* resolve as bare identifiers
# --------------------------------------------------------------------------


def test_enum_identifiers_resolve_and_are_distinct():
    names = [
        "JoinKind.Inner",
        "JoinKind.LeftOuter",
        "JoinKind.RightOuter",
        "JoinKind.FullOuter",
        "JoinKind.LeftAnti",
        "JoinKind.RightAnti",
        "GroupKind.Global",
        "GroupKind.Local",
    ]
    values = [evaluate(name) for name in names]
    assert len(set(values)) == len(names)  # all pairwise distinct
