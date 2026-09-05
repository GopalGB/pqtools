"""The 46 documented ``Table.*``/``Tables.*``/``ItemExpression.*``/
``RowExpression.*`` names Microsoft documents that pqtools did not have
before 0.10.0.

Every fixture here is written the way REAL M is written - ``#table({"a"},
{{1}})`` literals, ``each [Col]`` row lambdas, record-match navigation
(``[a=1]``) - never ``Table.FromRecords`` or a hand-rolled Python fixture.
0.9.0 shipped three bugs behind 1000+ green tests precisely because every
fixture used a library-author idiom instead of this one.

Three groups of names appear below:

1. Implemented, grounded against learn.microsoft.com/en-us/powerquery-m/
   <name-lowercased> (one page per function; cited in each implementation's
   own docstring/comment in ``_table.py``/``_table_shape.py``/
   ``_table_join.py``, not repeated here).
2. Refused by a function THIS package registers (``Table.ApproximateRowCount``,
   ``Table.PartitionValues``, ``Table.ColumnsOfType``) - real, documented
   functions whose real semantics need information this evaluator's
   in-memory, connector-less, untyped tables structurally do not carry.
3. Refused by pqtools' EXISTING catalog mechanism (``pqtools.catalog``) -
   these 11 names (``Table.View``/``ViewError``/``ViewFunction``,
   ``ItemExpression.From``, ``RowExpression.Column``, ``RowExpression.From``,
   the four ``*Fuzzy*`` functions, and ``Tables.GetRelationships``) are
   query-folding/engine/fuzzy-match/data-model infrastructure that
   Microsoft's own reference already classifies as such (folding/engine/
   fuzzy/model reason keys in ``pqtools/catalog.py``). They are deliberately
   NOT registered in ``BUILTINS`` by this file's own modules - the catalog's
   ``explain()`` already gives the exact right refusal message the moment a
   name is absent from the registry, so registering a second, competing
   refusal here would only shadow a better one that already exists.
"""

from __future__ import annotations

import pytest

from pqtools import EvalError, UnsupportedError, evaluate
from pqtools.builtins._table_shape import _KeyedTable


def run(expression: str) -> object:
    return evaluate(expression)


# ---------------------------------------------------------------------------
# Table.First / Table.FirstValue / Table.Last / Table.SingleRow
# ---------------------------------------------------------------------------


def test_first_returns_the_first_row_as_a_record() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-first
    assert run(
        """Table.First(#table({"CustomerID","Name"},{{1,"Bob"},{2,"Jim"},{3,"Paul"}}))"""
    ) == {"CustomerID": 1, "Name": "Bob"}


def test_first_returns_the_default_on_an_empty_table() -> None:
    assert run("""Table.First(#table({"a","b"},{}), [a=0,b=0])""") == {"a": 0, "b": 0}


def test_first_with_no_default_on_an_empty_table_is_null() -> None:
    # Not shown by any worked example on Microsoft's page - a deliberate
    # choice mirroring this codebase's own List.First/List.Last for the
    # same undocumented corner (null, not an error). Pinned here as that
    # choice, not a verified fact.
    assert run("""Table.First(#table({"a"},{}))""") is None


def test_last_returns_the_last_row_as_a_record() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-last
    assert run(
        """Table.Last(#table({"CustomerID","Name"},{{1,"Bob"},{2,"Jim"},{3,"Paul"}}))"""
    ) == {"CustomerID": 3, "Name": "Paul"}


def test_last_returns_the_default_on_an_empty_table() -> None:
    assert run("""Table.Last(#table({"a","b"},{}), [a=0,b=0])""") == {"a": 0, "b": 0}


def test_first_value_returns_the_first_field_of_the_first_row() -> None:
    assert run("""Table.FirstValue(#table({"a","b"},{{1,"x"},{2,"y"}}))""") == 1


def test_first_value_returns_the_default_on_an_empty_table() -> None:
    assert run("""Table.FirstValue(#table({"a"},{}), -1)""") == -1


def test_first_value_with_a_zero_column_row_falls_back_like_empty() -> None:
    # Undocumented corner: a table with rows but no columns has no "first
    # column" either. Treated the same as an empty table - a deliberate,
    # tested choice, not a documented rule.
    assert run("""Table.FirstValue(#table({}, {{}}), -1)""") == -1


def test_single_row_returns_the_one_row_as_a_record() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-singlerow
    assert run("""Table.SingleRow(#table({"CustomerID","Name"},{{1,"Bob"}}))""") == {
        "CustomerID": 1,
        "Name": "Bob",
    }


def test_single_row_errors_on_more_than_one_row() -> None:
    with pytest.raises(EvalError, match="expected exactly one row, got 2"):
        run("""Table.SingleRow(#table({"a"},{{1},{2}}))""")


def test_single_row_errors_on_zero_rows() -> None:
    # Not addressed by any worked example - forced by the function's own
    # non-nullable `as record` return type (unlike Table.First/Last's
    # `as any` with a default). Pinned as a choice.
    with pytest.raises(EvalError, match="expected exactly one row, got 0"):
        run("""Table.SingleRow(#table({"a"},{}))""")


# ---------------------------------------------------------------------------
# Table.RemoveFirstN / Table.RemoveLastN / Table.RemoveRows / Table.ReplaceRows
# ---------------------------------------------------------------------------

_FOUR = '#table({"CustomerID"},{{1},{2},{3},{4}})'


def test_remove_first_n_defaults_to_removing_one_row() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-removefirstn
    assert run(f"Table.RemoveFirstN({_FOUR})") == [
        {"CustomerID": 2},
        {"CustomerID": 3},
        {"CustomerID": 4},
    ]


def test_remove_first_n_with_a_count() -> None:
    assert run(f"Table.RemoveFirstN({_FOUR}, 2)") == [
        {"CustomerID": 3},
        {"CustomerID": 4},
    ]


def test_remove_first_n_with_a_condition_stops_at_first_failure() -> None:
    # Removes the LEADING run that satisfies the condition, not every
    # matching row (row 3 and 4 both fail <=2, but the doc's own worked
    # example never re-checks after the first failure).
    assert run(f"Table.RemoveFirstN({_FOUR}, each [CustomerID] <= 2)") == [
        {"CustomerID": 3},
        {"CustomerID": 4},
    ]


def test_remove_first_n_condition_must_be_logical() -> None:
    with pytest.raises(EvalError, match="condition must return a logical value"):
        run(f"Table.RemoveFirstN({_FOUR}, each [CustomerID])")


def test_remove_last_n_defaults_to_removing_one_row() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-removelastn
    assert run(f"Table.RemoveLastN({_FOUR})") == [
        {"CustomerID": 1},
        {"CustomerID": 2},
        {"CustomerID": 3},
    ]


def test_remove_last_n_with_a_condition_walks_backward_from_the_end() -> None:
    # The doc's own worked example: only CustomerID=1 survives, because the
    # backward walk removes 4, 3, 2 (all >= 2) and stops at 1 (which fails).
    assert run(f"Table.RemoveLastN({_FOUR}, each [CustomerID] >= 2)") == [
        {"CustomerID": 1}
    ]


def test_remove_rows_defaults_count_to_one() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-removerows
    assert run(f"Table.RemoveRows({_FOUR}, 1)") == [
        {"CustomerID": 1},
        {"CustomerID": 3},
        {"CustomerID": 4},
    ]


def test_remove_rows_with_an_explicit_count() -> None:
    assert run(f"Table.RemoveRows({_FOUR}, 1, 2)") == [
        {"CustomerID": 1},
        {"CustomerID": 4},
    ]


def test_remove_rows_offset_out_of_range_errors() -> None:
    with pytest.raises(EvalError, match="offset is out of range"):
        run(f"Table.RemoveRows({_FOUR}, 99)")


def test_replace_rows_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-replacerows: a 5-row
    # table, replace 3 rows starting at offset 1 with 2 new rows - net row
    # count shrinks by one.
    table = '#table({"Column1"},{{1},{2},{3},{4},{5}})'
    assert run(f"Table.ReplaceRows({table}, 1, 3, {{[Column1=6],[Column1=7]}})") == [
        {"Column1": 1},
        {"Column1": 6},
        {"Column1": 7},
        {"Column1": 5},
    ]


def test_replace_rows_requires_all_four_arguments() -> None:
    with pytest.raises(UnsupportedError, match="Table.ReplaceRows"):
        run('Table.ReplaceRows(#table({"a"},{{1}}), 0, 1)')


# ---------------------------------------------------------------------------
# Table.FindText / Table.PrefixColumns / Table.ColumnsOfType (refused)
# ---------------------------------------------------------------------------


def test_find_text_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-findtext
    table = (
        '#table({"CustomerID","Name","Phone"},'
        '{{1,"Bob","123-4567"},{2,"Jim","987-6543"},'
        '{3,"Paul","543-7890"},{4,"Ringo","232-1550"}})'
    )
    assert run(f'Table.FindText({table}, "Bob")') == [
        {"CustomerID": 1, "Name": "Bob", "Phone": "123-4567"}
    ]


def test_find_text_returns_an_empty_table_when_not_found() -> None:
    assert run('Table.FindText(#table({"a"},{{"x"}}), "zzz")') == []


def test_find_text_only_searches_text_cells_case_sensitively() -> None:
    # Deliberate, narrower-than-invented choice: Microsoft's only example is
    # all-text and states no stringification/case rule. Numbers are not
    # coerced to text, and the match is case-sensitive.
    assert run('Table.FindText(#table({"a"},{{123}}), "123")') == []
    assert run('Table.FindText(#table({"a"},{{"ABC"}}), "abc")') == []


def test_prefix_columns_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-prefixcolumns
    table = '#table({"CustomerID","Name","Phone"},{{1,"Bob","123-4567"}})'
    assert run(f'Table.PrefixColumns({table}, "MyTable")') == [
        {"MyTable.CustomerID": 1, "MyTable.Name": "Bob", "MyTable.Phone": "123-4567"}
    ]


def test_columns_of_type_refuses() -> None:
    # Matches a column's DECLARED type (Microsoft's own example ascribes one
    # via `type table[...]`) - this evaluator's tables carry no per-column
    # declared type independent of their row data.
    with pytest.raises(UnsupportedError, match="DECLARED type"):
        run('Table.ColumnsOfType(#table({"a"},{{1}}), {type number})')


# ---------------------------------------------------------------------------
# Table.CombineColumns / Table.CombineColumnsToRecord
# ---------------------------------------------------------------------------


def test_combine_columns_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-combinecolumns
    table = '#table({"FirstName","LastName"},{{"Bob","Smith"}})'
    assert run(
        f'Table.CombineColumns({table}, {{"LastName","FirstName"}}, '
        'Combiner.CombineTextByDelimiter(",", QuoteStyle.None), "FullName")'
    ) == [{"FullName": "Smith,Bob"}]


def test_combine_columns_keeps_untouched_columns_and_positions_at_first_source() -> (
    None
):
    table = '#table({"a","FirstName","LastName","z"},{{9,"Bob","Smith",1}})'
    assert run(
        f'Table.CombineColumns({table}, {{"FirstName","LastName"}}, '
        'Combiner.CombineTextByDelimiter(" ", QuoteStyle.None), "Full")'
    ) == [{"a": 9, "Full": "Bob Smith", "z": 1}]


def test_combine_columns_to_record_builds_a_record_of_the_source_columns() -> None:
    # No worked example on Microsoft's page - source-column-removal and
    # first-position placement are inferred from the sibling
    # Table.CombineColumns, pinned here as a choice.
    table = '#table({"FirstName","LastName"},{{"Bob","Smith"}})'
    assert run(
        f'Table.CombineColumnsToRecord({table}, "Person", {{"FirstName","LastName"}})'
    ) == [{"Person": {"FirstName": "Bob", "LastName": "Smith"}}]


def test_combine_columns_to_record_accepts_but_ignores_presentation_options() -> None:
    table = '#table({"FirstName","LastName"},{{"Bob","Smith"}})'
    assert run(
        f'Table.CombineColumnsToRecord({table}, "Person", {{"FirstName","LastName"}}, '
        '[DisplayNameColumn="FirstName", TypeName="Person"])'
    ) == [{"Person": {"FirstName": "Bob", "LastName": "Smith"}}]


def test_combine_columns_to_record_rejects_unknown_options() -> None:
    table = '#table({"a"},{{1}})'
    with pytest.raises(UnsupportedError, match="option"):
        run(f'Table.CombineColumnsToRecord({table}, "P", {{"a"}}, [Bogus=1])')


# ---------------------------------------------------------------------------
# Table.Split / Table.TransformRows
# ---------------------------------------------------------------------------


def test_split_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-split: 5 rows, pageSize
    # 2 -> a short last chunk, not padded or errored.
    table = '#table({"CustomerID"},{{1},{2},{3},{4},{5}})'
    assert run(f"Table.Split({table}, 2)") == [
        [{"CustomerID": 1}, {"CustomerID": 2}],
        [{"CustomerID": 3}, {"CustomerID": 4}],
        [{"CustomerID": 5}],
    ]


def test_split_rejects_a_non_positive_page_size() -> None:
    with pytest.raises(EvalError, match="pageSize must be a positive number"):
        run('Table.Split(#table({"a"},{{1}}), 0)')


def test_transform_rows_returns_a_list_not_a_table_field_shorthand() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-transformrows, example 1
    assert run(
        """Table.TransformRows(#table({"a"},{{1},{2},{3},{4},{5}}), each [a])"""
    ) == [1, 2, 3, 4, 5]


def test_transform_rows_can_build_new_records() -> None:
    # Same page, example 2 (explicit-parameter lambda building a record).
    assert run(
        """Table.TransformRows("""
        """#table({"a"},{{1},{2}}), (row) as record => [B = Text.From(row[a])])"""
    ) == [{"B": "1"}, {"B": "2"}]


# ---------------------------------------------------------------------------
# Table.AddKey / Table.Keys / Table.ReplaceKeys
# ---------------------------------------------------------------------------


def test_add_key_leaves_the_rows_unchanged() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-addkey
    table = '#table({"Id","Name"},{{1,"Hello There"},{2,"Good Bye"}})'
    assert run(f'Table.AddKey({table}, {{"Id"}}, true)') == [
        {"Id": 1, "Name": "Hello There"},
        {"Id": 2, "Name": "Good Bye"},
    ]


def test_keys_round_trips_through_add_key() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-keys
    table = '#table({"Id","Name"},{{1,"Hello There"},{2,"Good Bye"}})'
    assert run(f'Table.Keys(Table.AddKey({table}, {{"Id"}}, true))') == [
        {"Columns": ["Id"], "Primary": True}
    ]


def test_keys_of_a_plain_table_is_an_empty_list() -> None:
    assert run('Table.Keys(#table({"a"},{{1}}))') == []


def test_add_key_accumulates_multiple_candidate_keys() -> None:
    table = '#table({"Id","Code"},{{1,"A"}})'
    query = (
        f'Table.Keys(Table.AddKey(Table.AddKey({table}, {{"Id"}}, true), '
        '{"Code"}, false))'
    )
    assert run(query) == [
        {"Columns": ["Id"], "Primary": True},
        {"Columns": ["Code"], "Primary": False},
    ]


def test_replace_keys_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-replacekeys
    table = '#table({"Id","Name"},{{1,"Hello There"},{2,"Good Bye"}})'
    query = (
        f'Table.Keys(Table.ReplaceKeys(Table.AddKey({table}, {{"Id"}}, true), '
        '{[Columns={"Id"}, Primary=false]}))'
    )
    assert run(query) == [{"Columns": ["Id"], "Primary": False}]


def test_replace_keys_rejects_a_malformed_spec() -> None:
    table = '#table({"a"},{{1}})'
    with pytest.raises(EvalError, match="Columns"):
        run(f"Table.ReplaceKeys({table}, {{[NotColumns=1]}})")


def test_keys_do_not_survive_an_unrelated_transform() -> None:
    # Deliberate design choice, not a bug: metadata is attached to the
    # specific object AddKey returns, and does not propagate through a
    # fresh list built by an unrelated transform (see _KeyedTable's
    # docstring in _table_shape.py).
    table = '#table({"Id"},{{1}})'
    query = f'Table.Keys(Table.Sort(Table.AddKey({table}, {{"Id"}}, true), "Id"))'
    assert run(query) == []


# ---------------------------------------------------------------------------
# Table.PartitionKey / Table.ReplacePartitionKey
# ---------------------------------------------------------------------------


def test_partition_key_of_a_plain_table_is_null() -> None:
    assert run('Table.PartitionKey(#table({"a"},{{1}}))') is None


def test_replace_partition_key_round_trips_an_opaque_list() -> None:
    # No worked example exists anywhere on Microsoft's site for this pair -
    # stored and returned verbatim, uninterpreted, as a deliberate,
    # narrower choice (see _table_shape.py's section docstring).
    table = '#table({"a"},{{1}})'
    query = f'Table.PartitionKey(Table.ReplacePartitionKey({table}, {{"x", 1}}))'
    assert run(query) == ["x", 1]


def test_replace_partition_key_accepts_null() -> None:
    table = '#table({"a"},{{1}})'
    query = f"Table.PartitionKey(Table.ReplacePartitionKey({table}, null))"
    assert run(query) is None


def test_replace_partition_key_rejects_a_non_list_non_null_value() -> None:
    with pytest.raises(EvalError, match="must be a list or null"):
        run('Table.ReplacePartitionKey(#table({"a"},{{1}}), "nope")')


def test_add_key_and_replace_partition_key_do_not_clobber_each_other() -> None:
    table = '#table({"Id"},{{1}})'
    query = (
        f'let base = Table.AddKey({table}, {{"Id"}}, true), '
        "withPartition = Table.ReplacePartitionKey(base, {1}) "
        "in [keys = Table.Keys(withPartition), "
        "partitionKey = Table.PartitionKey(withPartition)]"
    )
    assert run(query) == {
        "keys": [{"Columns": ["Id"], "Primary": True}],
        "partitionKey": [1],
    }


# ---------------------------------------------------------------------------
# Table.FromPartitions / Table.Partition
# ---------------------------------------------------------------------------


def test_from_partitions_matches_the_microsoft_docs_nested_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-frompartitions - 3 levels
    # of nesting (Year -> Month -> Day), flattened into 4 rows.
    query = """
    Table.FromPartitions(
        "Year",
        {
            {
                1994,
                Table.FromPartitions(
                    "Month",
                    {
                        {
                            "Jan",
                            Table.FromPartitions(
                                "Day",
                                {
                                    {1, #table({"Foo"}, {{"Bar"}})},
                                    {2, #table({"Foo"}, {{"Bar"}})}
                                }
                            )
                        },
                        {
                            "Feb",
                            Table.FromPartitions(
                                "Day",
                                {
                                    {3, #table({"Foo"}, {{"Bar"}})},
                                    {4, #table({"Foo"}, {{"Bar"}})}
                                }
                            )
                        }
                    }
                )
            }
        }
    )
    """
    assert run(query) == [
        {"Foo": "Bar", "Day": 1, "Month": "Jan", "Year": 1994},
        {"Foo": "Bar", "Day": 2, "Month": "Jan", "Year": 1994},
        {"Foo": "Bar", "Day": 3, "Month": "Feb", "Year": 1994},
        {"Foo": "Bar", "Day": 4, "Month": "Feb", "Year": 1994},
    ]


def test_from_partitions_rejects_a_column_that_already_exists() -> None:
    with pytest.raises(EvalError, match="column already exists"):
        run('Table.FromPartitions("Foo", {{1, #table({"Foo"}, {{"Bar"}})}})')


def test_partition_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-partition
    table = '#table({"a","b"},{{2,4},{1,4},{2,4},{1,4}})'
    assert run(f'Table.Partition({table}, "a", 2, each _)') == [
        [{"a": 2, "b": 4}, {"a": 2, "b": 4}],
        [{"a": 1, "b": 4}, {"a": 1, "b": 4}],
    ]


def test_partition_requires_at_least_one_group() -> None:
    with pytest.raises(EvalError, match="groups must be at least 1"):
        run('Table.Partition(#table({"a"},{{1}}), "a", 0, each _)')


# ---------------------------------------------------------------------------
# Table.ApproximateRowCount / Table.PartitionValues (refused) / Table.StopFolding
# ---------------------------------------------------------------------------


def test_approximate_row_count_refuses() -> None:
    with pytest.raises(UnsupportedError, match="does not support approximation"):
        run('Table.ApproximateRowCount(#table({"a"},{{1}}))')


def test_partition_values_refuses() -> None:
    with pytest.raises(UnsupportedError, match="physical partitioning scheme"):
        run('Table.PartitionValues(#table({"a"},{{1}}))')


def test_stop_folding_is_identity() -> None:
    # "Prevents downstream operations from folding" is vacuously already
    # true here - this evaluator never folds anything.
    assert run('Table.StopFolding(#table({"a"},{{1},{2}}))') == [{"a": 1}, {"a": 2}]


def test_stop_folding_does_not_preserve_key_metadata() -> None:
    table = '#table({"Id"},{{1}})'
    query = f'Table.Keys(Table.StopFolding(Table.AddKey({table}, {{"Id"}}, true)))'
    assert run(query) == []


# ---------------------------------------------------------------------------
# Table.ContainsAll / Table.ContainsAny
# ---------------------------------------------------------------------------

_CUSTOMERS = (
    '#table({"CustomerID","Name","Phone"},'
    '{{1,"Bob","1"},{2,"Jim","2"},{3,"Paul","3"},{4,"Ringo","4"}})'
)


def test_contains_all_true_with_a_narrowing_equation_criteria() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-containsall, example 1:
    # full records would not match (different Name/no Phone), but
    # comparing only "CustomerID" makes it true.
    query = (
        f"Table.ContainsAll({_CUSTOMERS}, "
        '{[CustomerID=1,Name="Bill"],[CustomerID=2,Name="Fred"]}, "CustomerID")'
    )
    assert run(query) is True


def test_contains_all_false_on_full_record_match_by_default() -> None:
    # Same page, example 2: no equationCriteria -> full-record subset match,
    # and Name/Phone differ, so it is false.
    query = (
        f"Table.ContainsAll({_CUSTOMERS}, "
        '{[CustomerID=1,Name="Bill"],[CustomerID=2,Name="Fred"]})'
    )
    assert run(query) is False


def test_contains_any_true_on_a_single_full_match() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-containsany, example 1
    table = '#table({"a","b"},{{1,2},{3,4}})'
    assert run(f"Table.ContainsAny({table}, {{[a=1,b=2],[a=3,b=5]}})") is True


def test_contains_any_false_when_nothing_matches() -> None:
    table = '#table({"a","b"},{{1,2},{3,4}})'
    assert run(f"Table.ContainsAny({table}, {{[a=1,b=3],[a=3,b=5]}})") is False


def test_contains_any_true_with_equation_criteria_narrowing_to_one_column() -> None:
    table = '#table({"a","b"},{{1,2},{3,4}})'
    query = f'Table.ContainsAny({table}, {{[a=1,b=3],[a=3,b=5]}}, "a")'
    assert run(query) is True


def test_contains_all_and_any_accept_a_list_of_criteria_columns() -> None:
    # Deliberate generalisation of the one documented (bare-name) shape,
    # matching this codebase's uniform "_field_name_list" convention for
    # every other one-or-many-columns parameter. Pinned as a choice.
    table = '#table({"a","b","c"},{{1,2,9}})'
    query = f'Table.ContainsAny({table}, {{[a=1,b=2,c=0]}}, {{"a","b"}})'
    assert run(query) is True


# ---------------------------------------------------------------------------
# Table.IsDistinct
# ---------------------------------------------------------------------------


def test_is_distinct_true_when_every_row_differs() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-isdistinct, example 1
    assert run('Table.IsDistinct(#table({"a"},{{1},{2},{3},{4}}))') is True


def test_is_distinct_false_with_a_named_column_duplicate() -> None:
    # Same page, example 2: a duplicate Name value makes it false even
    # though the rows are not otherwise identical.
    table = '#table({"Id","Name"},{{1,"Bob"},{2,"Jim"},{3,"Paul"},{4,"Bob"}})'
    assert run(f'Table.IsDistinct({table}, "Name")') is False


def test_is_distinct_true_with_a_named_column_when_that_column_has_no_dupes() -> None:
    table = '#table({"Id","Name"},{{1,"Bob"},{2,"Jim"},{3,"Paul"},{4,"Bob"}})'
    assert run(f'Table.IsDistinct({table}, "Id")') is True


# ---------------------------------------------------------------------------
# Table.PositionOfAny
# ---------------------------------------------------------------------------


def test_position_of_any_returns_the_first_match_by_default() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-positionofany, example 1
    table = '#table({"a","b"},{{2,4},{1,4},{2,4},{1,4}})'
    assert run(f"Table.PositionOfAny({table}, {{[a=2,b=4],[a=6,b=8]}})") == 0


def test_position_of_any_with_occurrence_all() -> None:
    # Same page, example 2
    table = '#table({"a","b"},{{2,4},{6,8},{2,4},{1,4}})'
    query = f"Table.PositionOfAny({table}, {{[a=2,b=4],[a=6,b=8]}}, Occurrence.All)"
    assert run(query) == [0, 1, 2]


def test_position_of_any_returns_minus_one_when_absent() -> None:
    table = '#table({"a"},{{1},{2}})'
    assert run(f"Table.PositionOfAny({table}, {{[a=99]}})") == -1


def test_position_of_any_occurrence_last() -> None:
    table = '#table({"a"},{{1},{2},{1}})'
    query = f"Table.PositionOfAny({table}, {{[a=1]}}, Occurrence.Last)"
    assert run(query) == 2


# ---------------------------------------------------------------------------
# Table.ReplaceMatchingRows
# ---------------------------------------------------------------------------


def test_replace_matching_rows_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-replacematchingrows: both
    # occurrences of the duplicated row get replaced.
    table = '#table({"a","b"},{{1,2},{2,3},{3,4},{1,2}})'
    query = (
        f"Table.ReplaceMatchingRows({table}, "
        "{{[a=1,b=2],[a=-1,b=-2]}, {[a=2,b=3],[a=-2,b=-3]}})"
    )
    assert run(query) == [
        {"a": -1, "b": -2},
        {"a": -2, "b": -3},
        {"a": 3, "b": 4},
        {"a": -1, "b": -2},
    ]


def test_replace_matching_rows_leaves_non_matching_rows_untouched() -> None:
    table = '#table({"a"},{{1},{2}})'
    query = f"Table.ReplaceMatchingRows({table}, {{{{[a=99],[a=100]}}}})"
    assert run(query) == [{"a": 1}, {"a": 2}]


def test_replace_matching_rows_first_spec_wins_on_overlap() -> None:
    # Undocumented corner (no example covers one row matching two specs) -
    # pinned as a choice: first-in-list-order wins.
    table = '#table({"a"},{{1}})'
    query = (
        f"Table.ReplaceMatchingRows({table}, {{{{[a=1],[a=100]}}, {{[a=1],[a=200]}}}})"
    )
    assert run(query) == [{"a": 100}]


# ---------------------------------------------------------------------------
# Table.AddRankColumn
# ---------------------------------------------------------------------------


def test_add_rank_column_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-addrankcolumn - note the
    # output is REORDERED to rank order (Paul moves ahead of Jim).
    table = (
        '#table({"CustomerID","Name","Revenue"},'
        '{{1,"Bob",200},{2,"Jim",100},{3,"Paul",200},{4,"Ringo",50}})'
    )
    query = (
        f'Table.AddRankColumn({table}, "RevenueRank", {{"Revenue", Order.Descending}}, '
        "[RankKind = RankKind.Competition])"
    )
    assert run(query) == [
        {"CustomerID": 1, "Name": "Bob", "Revenue": 200, "RevenueRank": 1},
        {"CustomerID": 3, "Name": "Paul", "Revenue": 200, "RevenueRank": 1},
        {"CustomerID": 2, "Name": "Jim", "Revenue": 100, "RevenueRank": 3},
        {"CustomerID": 4, "Name": "Ringo", "Revenue": 50, "RevenueRank": 4},
    ]


def test_add_rank_column_dense_leaves_no_gap() -> None:
    table = '#table({"a"},{{200},{100},{200},{50}})'
    query = (
        f'Table.AddRankColumn({table}, "r", {{"a", Order.Descending}}, '
        "[RankKind = RankKind.Dense])"
    )
    assert [r["r"] for r in run(query)] == [1, 1, 2, 3]  # type: ignore[index]


def test_add_rank_column_ordinal_never_ties() -> None:
    table = '#table({"a"},{{200},{100},{200},{50}})'
    query = (
        f'Table.AddRankColumn({table}, "r", {{"a", Order.Descending}}, '
        "[RankKind = RankKind.Ordinal])"
    )
    assert [r["r"] for r in run(query)] == [1, 2, 3, 4]  # type: ignore[index]


def test_add_rank_column_defaults_to_competition_when_options_omitted() -> None:
    # Genuinely undocumented default (Microsoft's one example always passes
    # RankKind explicitly) - pinned as a named, tested choice.
    table = '#table({"a"},{{200},{100},{200},{50}})'
    query = f'Table.AddRankColumn({table}, "r", {{"a", Order.Descending}})'
    assert [r["r"] for r in run(query)] == [1, 1, 3, 4]  # type: ignore[index]


def test_add_rank_column_rejects_an_existing_column_name() -> None:
    table = '#table({"a"},{{1}})'
    with pytest.raises(EvalError, match="column already exists"):
        run(f'Table.AddRankColumn({table}, "a", "a")')


# ---------------------------------------------------------------------------
# Table.MaxN / Table.MinN
# ---------------------------------------------------------------------------

_ABN = '#table({"a","b"},{{2,4},{0,0},{6,2}})'


def test_max_n_with_a_condition_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-maxn - largest-first,
    # contradicting the page's own "ascending order" prose (a copy-paste
    # artifact from Table.MinN's page); the worked example is followed.
    assert run(f'Table.MaxN({_ABN}, "a", each [a] > 0)') == [
        {"a": 6, "b": 2},
        {"a": 2, "b": 4},
    ]


def test_max_n_condition_failing_immediately_returns_empty() -> None:
    assert run(f'Table.MaxN({_ABN}, "a", each [b] > 100)') == []


def test_max_n_with_a_count() -> None:
    assert run(f'Table.MaxN({_ABN}, "a", 2)') == [{"a": 6, "b": 2}, {"a": 2, "b": 4}]


def test_min_n_with_a_condition_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-minn
    assert run(f'Table.MinN({_ABN}, "a", each [a] < 3)') == [
        {"a": 0, "b": 0},
        {"a": 2, "b": 4},
    ]


def test_min_n_with_a_count() -> None:
    assert run(f'Table.MinN({_ABN}, "a", 2)') == [{"a": 0, "b": 0}, {"a": 2, "b": 4}]


def test_max_n_and_min_n_reject_a_negative_count() -> None:
    with pytest.raises(EvalError, match="must not be negative"):
        run(f'Table.MaxN({_ABN}, "a", -1)')
    with pytest.raises(EvalError, match="must not be negative"):
        run(f'Table.MinN({_ABN}, "a", -1)')


# ---------------------------------------------------------------------------
# Table.AddJoinColumn
# ---------------------------------------------------------------------------


def test_add_join_column_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-addjoincolumn - the new
    # column holds the FULL matching row(s), including columns beyond the
    # join key, nested one level (identical to Table.NestedJoin's shape).
    sales = '#table({"saleID","item"},{{1,"Shirt"},{2,"Hat"}})'
    prices = '#table({"saleID","price","stock"},{{1,20,1234},{2,10,5643}})'
    query = f'Table.AddJoinColumn({sales}, "saleID", {prices}, "saleID", "price")'
    assert run(query) == [
        {
            "saleID": 1,
            "item": "Shirt",
            "price": [{"saleID": 1, "price": 20, "stock": 1234}],
        },
        {
            "saleID": 2,
            "item": "Hat",
            "price": [{"saleID": 2, "price": 10, "stock": 5643}],
        },
    ]


def test_add_join_column_is_left_outer_unmatched_rows_survive() -> None:
    sales = '#table({"saleID"},{{1},{2}})'
    prices = '#table({"saleID","price"},{{1,20}})'
    query = f'Table.AddJoinColumn({sales}, "saleID", {prices}, "saleID", "match")'
    assert run(query) == [
        {"saleID": 1, "match": [{"saleID": 1, "price": 20}]},
        {"saleID": 2, "match": []},
    ]


# ---------------------------------------------------------------------------
# Table.AggregateTableColumn
# ---------------------------------------------------------------------------


def test_aggregate_table_column_matches_the_microsoft_docs_example() -> None:
    # learn.microsoft.com/en-us/powerquery-m/table-aggregatetablecolumn
    nested = '#table({"a","b","c"},{{1,2,3},{2,4,6}})'
    outer = f'#table({{"t","b"}},{{{{{nested},2}}}})'
    query = (
        f'Table.AggregateTableColumn({outer}, "t", '
        '{{"a",List.Sum,"sum of t.a"},{"b",List.Min,"min of t.b"},'
        '{"b",List.Max,"max of t.b"},{"a",List.Count,"count of t.a"}})'
    )
    assert run(query) == [
        {
            "sum of t.a": 3,
            "min of t.b": 2,
            "max of t.b": 4,
            "count of t.a": 2,
            "b": 2,
        }
    ]


def test_aggregate_table_column_rejects_a_duplicate_output_name() -> None:
    nested = '#table({"a"},{{1}})'
    outer = f'#table({{"t"}},{{{{{nested}}}}})'
    query = (
        f'Table.AggregateTableColumn({outer}, "t", '
        '{{"a",List.Sum,"x"},{"a",List.Count,"x"}})'
    )
    with pytest.raises(EvalError, match="duplicate aggregate column"):
        run(query)


# ---------------------------------------------------------------------------
# Refused, not approximated - functions THIS package registers to refuse
# were tested above (ApproximateRowCount, PartitionValues, ColumnsOfType).
# The remaining 11 documented names are refused by pqtools' EXISTING
# catalog mechanism (pqtools.catalog.explain) - deliberately NOT registered
# a second time in _table.py/_table_join.py, since the catalog already
# gives the exact right, more specific message the moment a name is absent
# from BUILTINS.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Table.View", "query folding"),
        ("Table.ViewError", "query folding"),
        ("Table.ViewFunction", "query folding"),
        ("ItemExpression.From", "Mashup Engine"),
        ("RowExpression.Column", "Mashup Engine"),
        ("RowExpression.From", "Mashup Engine"),
        ("Table.FuzzyGroup", "approximate matching"),
        ("Table.FuzzyJoin", "approximate matching"),
        ("Table.FuzzyNestedJoin", "approximate matching"),
        ("Table.AddFuzzyClusterColumn", "approximate matching"),
        ("Tables.GetRelationships", "Power BI data model"),
    ],
)
def test_folding_engine_fuzzy_and_model_names_refuse_via_the_catalog(
    name: str, expected: str
) -> None:
    with pytest.raises(UnsupportedError, match=expected):
        evaluate(f"{name}(1)")


def test_the_eleven_catalog_refused_names_are_not_double_registered() -> None:
    """They must stay ABSENT from BUILTINS - registering them here too
    would shadow the catalog's own, more specific refusal message with a
    generic one, for no benefit."""
    from pqtools.evaluate import BUILTINS

    for name in (
        "Table.View",
        "Table.ViewError",
        "Table.ViewFunction",
        "ItemExpression.From",
        "RowExpression.Column",
        "RowExpression.From",
        "Table.FuzzyGroup",
        "Table.FuzzyJoin",
        "Table.FuzzyNestedJoin",
        "Table.AddFuzzyClusterColumn",
        "Tables.GetRelationships",
    ):
        assert name not in BUILTINS


def test_keyed_table_metadata_is_a_real_list_not_a_lookalike() -> None:
    """`_require_table`/equality/every downstream consumer must treat a
    `_KeyedTable` exactly like a plain table value - it IS one."""
    table = _KeyedTable([{"a": 1}])
    assert table == [{"a": 1}]
    assert isinstance(table, list)
