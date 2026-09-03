"""Tests for ``src/pqtools/builtins/_table_shape.py`` - reshaping (Unpivot/
Pivot/Transpose/SplitColumn) and everyday Table.* verbs. See
PRD-0.5.0-builtins.md.

Several assertions here are transcribed directly from Microsoft's own
``learn.microsoft.com/en-us/powerquery-m/...`` reference examples (fetched
while implementing this module) rather than invented, per the PRD's
"match real Power Query semantics, not intuition" rule; each such test says
so. Others pin edge cases this implementation had to reason through because
the docs did not spell them out exactly (also PRD-mandated) - those say so
too, with the reasoning.
"""

from __future__ import annotations

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Table.Unpivot / Table.UnpivotOtherColumns
# --------------------------------------------------------------------------


def test_unpivot_matches_the_microsoft_docs_example():
    """learn.microsoft.com/en-us/powerquery-m/table-unpivot's worked example:
    null cells are dropped, kept columns come first, row order preserved."""
    query = """
    Table.Unpivot(
        Table.FromRecords({
            [key = "x", a = 1, b = null, c = 3],
            [key = "y", a = 2, b = 4, c = null]
        }),
        {"a", "b", "c"},
        "attribute",
        "value"
    )
    """
    assert evaluate(query) == [
        {"key": "x", "attribute": "a", "value": 1},
        {"key": "x", "attribute": "c", "value": 3},
        {"key": "y", "attribute": "a", "value": 2},
        {"key": "y", "attribute": "b", "value": 4},
    ]


def test_unpivot_keeps_zero_and_empty_string_values():
    """Only null is dropped - zero and "" are real values and survive."""
    query = """
    Table.Unpivot(
        Table.FromRecords({[k = 1, a = 0, b = ""]}),
        {"a", "b"},
        "attr",
        "val"
    )
    """
    assert evaluate(query) == [
        {"k": 1, "attr": "a", "val": 0},
        {"k": 1, "attr": "b", "val": ""},
    ]


def test_unpivot_other_columns_matches_the_04_fixture_shape():
    """Same column order/null behaviour, driven from a "keep these" list -
    this is the exact shape tests/fixtures/realworld/04's query emits."""
    query = """
    Table.UnpivotOtherColumns(
        Table.FromRecords({
            [Region = "East", Date = "d1", Electronics = 500, Clothing = 0, Home = 120],
            [Region = "West", Date = "d2", Electronics = 0, Clothing = 300, Home = 0]
        }),
        {"Region", "Date"},
        "Category",
        "Revenue"
    )
    """
    result = evaluate(query)
    assert result == [
        {"Region": "East", "Date": "d1", "Category": "Electronics", "Revenue": 500},
        {"Region": "East", "Date": "d1", "Category": "Clothing", "Revenue": 0},
        {"Region": "East", "Date": "d1", "Category": "Home", "Revenue": 120},
        {"Region": "West", "Date": "d2", "Category": "Electronics", "Revenue": 0},
        {"Region": "West", "Date": "d2", "Category": "Clothing", "Revenue": 300},
        {"Region": "West", "Date": "d2", "Category": "Home", "Revenue": 0},
    ]


def test_unpivot_rejects_a_missing_column():
    with pytest.raises(EvalError, match="no such column: z"):
        evaluate('Table.Unpivot(Table.FromRecords({[a=1]}), {"z"}, "attr", "val")')


def test_unpivot_rejects_a_name_clash_with_a_kept_column():
    with pytest.raises(EvalError, match="clashes with a kept column"):
        evaluate('Table.Unpivot(Table.FromRecords({[a=1,b=2]}), {"b"}, "a", "val")')


# --------------------------------------------------------------------------
# Table.Pivot
# --------------------------------------------------------------------------


def test_pivot_basic_single_value_per_group():
    query = """
    Table.Pivot(
        Table.FromRecords({
            [Region = "East", Category = "A", Amount = 10],
            [Region = "East", Category = "B", Amount = 20],
            [Region = "West", Category = "A", Amount = 30]
        }),
        {"A", "B"},
        "Category",
        "Amount"
    )
    """
    assert evaluate(query) == [
        {"Region": "East", "A": 10, "B": 20},
        {"Region": "West", "A": 30, "B": None},
    ]


def test_pivot_missing_pivot_value_is_null():
    """A pivotValues entry with no matching attribute anywhere -> null."""
    query = """
    Table.Pivot(
        Table.FromRecords({[Region = "East", Category = "A", Amount = 10]}),
        {"A", "Z"},
        "Category",
        "Amount"
    )
    """
    assert evaluate(query) == [{"Region": "East", "A": 10, "Z": None}]


def test_pivot_multiple_values_without_aggregation_function_errors():
    """PRD correctness rule: real Power Query errors here rather than
    silently picking one value - this must match, not guess."""
    query = """
    Table.Pivot(
        Table.FromRecords({
            [Region = "East", Category = "A", Amount = 10],
            [Region = "East", Category = "A", Amount = 15]
        }),
        {"A"},
        "Category",
        "Amount"
    )
    """
    with pytest.raises(EvalError, match="supply an aggregation function"):
        evaluate(query)


def test_pivot_multiple_values_with_aggregation_function_sums():
    query = """
    Table.Pivot(
        Table.FromRecords({
            [Region = "East", Category = "A", Amount = 10],
            [Region = "East", Category = "A", Amount = 15],
            [Region = "East", Category = "B", Amount = 1]
        }),
        {"A", "B"},
        "Category",
        "Amount",
        List.Sum
    )
    """
    assert evaluate(query) == [{"Region": "East", "A": 25, "B": 1}]


def test_pivot_column_order_is_keys_then_pivot_values_in_given_order():
    """Key columns first (original order), then pivotValues in the order
    given - NOT sorted, NOT the order attribute values first appear."""
    query = """
    Table.Pivot(
        Table.FromRecords({[k1 = 1, k2 = 2, Category = "B", Amount = 1]}),
        {"B", "A"},
        "Category",
        "Amount"
    )
    """
    assert list(evaluate(query)[0].keys()) == ["k1", "k2", "B", "A"]


# --------------------------------------------------------------------------
# Table.Transpose
# --------------------------------------------------------------------------


def test_transpose_matches_the_microsoft_docs_example():
    """learn.microsoft.com/en-us/powerquery-m/table-transpose: original
    column NAMES are discarded entirely, only the data grid is transposed,
    result columns are named Column1..ColumnN."""
    query = """
    Table.Transpose(
        Table.FromRecords({
            [Name = "Full Name", Value = "Fred"],
            [Name = "Age", Value = 42],
            [Name = "Country", Value = "UK"]
        })
    )
    """
    assert evaluate(query) == [
        {"Column1": "Full Name", "Column2": "Age", "Column3": "Country"},
        {"Column1": "Fred", "Column2": 42, "Column3": "UK"},
    ]


def test_transpose_of_an_empty_table_is_empty():
    assert evaluate("Table.Transpose({})") == []


# --------------------------------------------------------------------------
# Table.FillDown / Table.FillUp
# --------------------------------------------------------------------------


def test_fill_down_fills_from_the_previous_non_null():
    query = """
    Table.FillDown(
        Table.FromRecords({[a=1],[a=null],[a=null],[a=3],[a=null]}),
        {"a"}
    )
    """
    assert [r["a"] for r in evaluate(query)] == [1, 1, 1, 3, 3]


def test_fill_down_leaves_a_leading_null_unfilled():
    query = 'Table.FillDown(Table.FromRecords({[a=null],[a=1]}), {"a"})'
    assert [r["a"] for r in evaluate(query)] == [None, 1]


def test_fill_up_fills_from_the_next_non_null():
    query = """
    Table.FillUp(
        Table.FromRecords({[a=null],[a=null],[a=3],[a=null],[a=5]}),
        {"a"}
    )
    """
    assert [r["a"] for r in evaluate(query)] == [3, 3, 3, 5, 5]


# --------------------------------------------------------------------------
# Table.AddIndexColumn
# --------------------------------------------------------------------------


def test_add_index_column_default_zero_based():
    query = 'Table.AddIndexColumn(Table.FromRecords({[a=1],[a=2],[a=3]}), "i")'
    assert [r["i"] for r in evaluate(query)] == [0, 1, 2]


def test_add_index_column_with_initial_and_increment():
    query = 'Table.AddIndexColumn(Table.FromRecords({[a=1],[a=2],[a=3]}), "i", 10, 5)'
    assert [r["i"] for r in evaluate(query)] == [10, 15, 20]


def test_add_index_column_existing_name_errors():
    with pytest.raises(EvalError, match="already exists"):
        evaluate('Table.AddIndexColumn(Table.FromRecords({[a=1]}), "a")')


# --------------------------------------------------------------------------
# Table.SplitColumn + Splitter.*
# --------------------------------------------------------------------------


def test_split_column_matches_the_microsoft_docs_example_with_auto_naming():
    """learn.microsoft.com/en-us/powerquery-m/table-splitcolumn Example 1:
    no columnNamesOrNumber given -> auto-detected column count (2, the max
    across the column) named "Name.1"/"Name.2"; a short row's missing slot
    is null."""
    # The docs build the source table with #table(type table[...], {...});
    # that construction syntax is not part of this evaluator's AST support
    # (only Table.FromRecords is), so the same data is built that way here.
    query = """
    Table.SplitColumn(
        Table.FromRecords({
            [CustomerID = 1, Name = "Bob White", Phone = "123-4567"],
            [CustomerID = 2, Name = "Jim Smith", Phone = "987-6543"],
            [CustomerID = 3, Name = "Paul", Phone = "543-7890"],
            [CustomerID = 4, Name = "Cristina Best", Phone = "232-1550"]
        }),
        "Name",
        Splitter.SplitTextByDelimiter(" ")
    )
    """
    result = evaluate(query)
    assert list(result[0].keys()) == ["CustomerID", "Name.1", "Name.2", "Phone"]
    assert result == [
        {"CustomerID": 1, "Name.1": "Bob", "Name.2": "White", "Phone": "123-4567"},
        {"CustomerID": 2, "Name.1": "Jim", "Name.2": "Smith", "Phone": "987-6543"},
        {"CustomerID": 3, "Name.1": "Paul", "Name.2": None, "Phone": "543-7890"},
        {
            "CustomerID": 4,
            "Name.1": "Cristina",
            "Name.2": "Best",
            "Phone": "232-1550",
        },
    ]


def test_split_column_with_explicit_names_and_default():
    """Docs Example 3: explicit new column names + a default fill value."""
    query = """
    Table.SplitColumn(
        Table.FromRecords({[Name = "Paul"]}),
        "Name",
        Splitter.SplitTextByDelimiter(" "),
        {"First Name", "Last Name"},
        "-No Entry-"
    )
    """
    assert evaluate(query) == [{"First Name": "Paul", "Last Name": "-No Entry-"}]


def test_split_column_extra_values_list_matches_the_microsoft_docs_example():
    """Docs Example 4: with ExtraValues.List, the LAST declared column is
    always a list of the remaining pieces once a piece reaches that slot -
    even a single remaining piece is wrapped ({"White"}, not "White")."""
    query = """
    Table.SplitColumn(
        Table.FromRecords({
            [Name = "Bob White"],
            [Name = "Cristina J. Best"]
        }),
        "Name",
        Splitter.SplitTextByDelimiter(" "),
        {"First Name", "Last Name"},
        null,
        ExtraValues.List
    )
    """
    assert evaluate(query) == [
        {"First Name": "Bob", "Last Name": ["White"]},
        {"First Name": "Cristina", "Last Name": ["J.", "Best"]},
    ]


def test_split_column_extra_values_list_with_too_few_pieces_uses_default():
    """Edge case the docs don't spell out: a row with FEWER pieces than
    declared columns gets `default` in the last slot, not an empty list -
    "no piece reaches that slot" is different from "one piece is left
    over", and only the latter is what ExtraValues.List wraps."""
    query = """
    Table.SplitColumn(
        Table.FromRecords({[Name = "Paul"]}),
        "Name",
        Splitter.SplitTextByDelimiter(" "),
        {"First Name", "Last Name"},
        null,
        ExtraValues.List
    )
    """
    assert evaluate(query) == [{"First Name": "Paul", "Last Name": None}]


def test_split_column_extra_values_error_raises():
    query = """
    Table.SplitColumn(
        Table.FromRecords({[Name = "Cristina J. Best"]}),
        "Name",
        Splitter.SplitTextByDelimiter(" "),
        {"First Name", "Last Name"},
        null,
        ExtraValues.Error
    )
    """
    with pytest.raises(EvalError, match="more split values"):
        evaluate(query)


def test_splitter_split_text_by_delimiter_quote_style_csv():
    query = """
    Splitter.SplitTextByDelimiter(",", QuoteStyle.Csv)("a,""b,c"",d")
    """
    assert evaluate(query) == ["a", "b,c", "d"]


def test_splitter_split_text_by_delimiter_rejects_empty_delimiter():
    with pytest.raises(EvalError, match="must not be empty"):
        evaluate('Splitter.SplitTextByDelimiter("")')


def test_splitter_split_text_by_each_delimiter_matches_microsoft_docs_example_1():
    """learn.microsoft.com/en-us/powerquery-m/splitter-splittextbyeachdelimiter
    Example 1: each delimiter is applied once, in sequence, not repeatedly."""
    query = 'Splitter.SplitTextByEachDelimiter({",", ";"})("a,b;c,d")'
    assert evaluate(query) == ["a", "b", "c,d"]


def test_splitter_split_text_by_each_delimiter_matches_microsoft_docs_example_2():
    """Same docs page, Example 2 (startAtEnd=true, quotes treated as plain
    characters): the text and each delimiter are processed from the end."""
    query = (
        'Splitter.SplitTextByEachDelimiter({",", ";"}, QuoteStyle.None, true)('
        + ('"a,""b;c"",d"')
        + ")"
    )
    assert evaluate(query) == ['a,"b', 'c"', "d"]


def test_splitter_split_text_by_positions_matches_microsoft_docs_example_1():
    query = "Splitter.SplitTextByPositions({0, 3, 4})(" + '"ABC|12345"' + ")"
    assert evaluate(query) == ["ABC", "|", "12345"]


def test_splitter_split_text_by_positions_matches_microsoft_docs_example_2():
    query = "Splitter.SplitTextByPositions({0, 5}, true)(" + '"Redmond98052"' + ")"
    assert evaluate(query) == ["Redmond", "98052"]


def test_splitter_split_text_by_character_transition_matches_microsoft_docs_example():
    """learn.microsoft.com/en-us/powerquery-m/splitter-splittextbycharactertransition
    - character classes given as explicit lists (the `..` range-literal form
    the docs use is a separate, unrelated evaluator construct)."""
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    digits = list("0123456789")
    letters_literal = "{" + ",".join(f'"{c}"' for c in letters) + "}"
    digits_literal = "{" + ",".join(f'"{c}"' for c in digits) + "}"
    query = (
        f"Splitter.SplitTextByCharacterTransition({letters_literal}, "
        f'{digits_literal})("Abc123")'
    )
    assert evaluate(query) == ["Abc", "123"]


def test_splitter_split_text_by_character_transition_accepts_a_function_class():
    """before/after may also be a function (character -> logical), per the
    real signature - pin this since the docs example only shows lists."""
    query = 'Splitter.SplitTextByCharacterTransition(each _ = "a", each _ = "b")("aab")'
    assert evaluate(query) == ["aa", "b"]


# --------------------------------------------------------------------------
# Table.ReplaceValue + Replacer.*
# --------------------------------------------------------------------------


def test_replace_value_exact_match_only():
    """Docs Example 1: Replacer.ReplaceValue matches the WHOLE value."""
    query = """
    Table.ReplaceValue(
        Table.FromRecords({[A=1,B="hello"],[A=2,B="goodbye"],[A=3,B="goodbyes"]}),
        "goodbye",
        "world",
        Replacer.ReplaceValue,
        {"B"}
    )
    """
    assert [r["B"] for r in evaluate(query)] == ["hello", "world", "goodbyes"]


def test_replace_text_matches_any_part_of_the_value():
    """Docs Example 2: Replacer.ReplaceText is a substring replace."""
    query = """
    Table.ReplaceValue(
        Table.FromRecords({[A=1,B="hello"],[A=2,B="wurld"]}),
        "ur",
        "or",
        Replacer.ReplaceText,
        {"B"}
    )
    """
    assert [r["B"] for r in evaluate(query)] == ["hello", "world"]


def test_replace_value_old_and_new_may_be_per_row_lambdas():
    """Docs Example 3's shape: oldValue/newValue can be `each`-lambdas
    evaluated once PER ROW (with `_` = the row), not just constants."""
    query = """
    Table.ReplaceValue(
        Table.FromRecords({[Name="Cindy",Country="US"],[Name="Bob",Country="CA"]}),
        each if [Country] = "US" then [Name] else false,
        "REDACTED",
        Replacer.ReplaceValue,
        {"Name"}
    )
    """
    assert evaluate(query) == [
        {"Name": "REDACTED", "Country": "US"},
        {"Name": "Bob", "Country": "CA"},
    ]


def test_replace_value_accepts_a_custom_replacer_function():
    query = """
    Table.ReplaceValue(
        Table.FromRecords({[A=1],[A=2],[A=3]}),
        2,
        99,
        (current, old, new) => if current = old then new else current * 10,
        {"A"}
    )
    """
    assert [r["A"] for r in evaluate(query)] == [10, 99, 30]


def test_replace_text_on_non_text_current_value_errors():
    query = """
    Table.ReplaceValue(
        Table.FromRecords({[A=1]}), "1", "2", Replacer.ReplaceText, {"A"}
    )
    """
    with pytest.raises(EvalError, match="must be text"):
        evaluate(query)


def test_replace_text_on_null_current_value_is_null():
    query = """
    Table.ReplaceValue(
        Table.FromRecords({[A=null]}), "1", "2", Replacer.ReplaceText, {"A"}
    )
    """
    assert evaluate(query) == [{"A": None}]


# --------------------------------------------------------------------------
# Everyday verbs
# --------------------------------------------------------------------------


def test_skip_no_args_skips_one_row():
    assert evaluate("Table.Skip(Table.FromRecords({[a=1],[a=2],[a=3]}))") == [
        {"a": 2},
        {"a": 3},
    ]


def test_skip_with_count():
    assert evaluate("Table.Skip(Table.FromRecords({[a=1],[a=2],[a=3]}), 2)") == [
        {"a": 3}
    ]


def test_skip_with_condition_stops_at_first_false():
    query = "Table.Skip(Table.FromRecords({[a=1],[a=2],[a=5],[a=1]}), each [a] < 3)"
    assert [r["a"] for r in evaluate(query)] == [5, 1]


def test_range_with_offset_and_count():
    query = "Table.Range(Table.FromRecords({[a=1],[a=2],[a=3],[a=4]}), 1, 2)"
    assert [r["a"] for r in evaluate(query)] == [2, 3]


def test_range_out_of_bounds_errors():
    with pytest.raises(EvalError, match="out of range"):
        evaluate("Table.Range(Table.FromRecords({[a=1]}), 0, 5)")


def test_reorder_columns_partial_list_appends_the_rest():
    """A partial column list is common real usage: named columns move to
    the front, everything else keeps its relative order after them."""
    query = 'Table.ReorderColumns(Table.FromRecords({[a=1,b=2,c=3]}), {"c"})'
    assert list(evaluate(query)[0].keys()) == ["c", "a", "b"]


def test_duplicate_column_basic():
    query = 'Table.DuplicateColumn(Table.FromRecords({[a=1,b=2]}), "a", "a2")'
    assert evaluate(query) == [{"a": 1, "b": 2, "a2": 1}]


def test_duplicate_column_four_arg_form_is_unsupported():
    with pytest.raises(UnsupportedError, match="newColumnNames"):
        evaluate('Table.DuplicateColumn(Table.FromRecords({[a=1]}), "a", "a2", {"a3"})')


def test_combine_fills_missing_columns_with_null_and_orders_first_table_first():
    query = """
    Table.Combine({
        Table.FromRecords({[a=1,b=2]}),
        Table.FromRecords({[b=3,c=4]})
    })
    """
    result = evaluate(query)
    assert list(result[0].keys()) == ["a", "b", "c"]
    assert result == [
        {"a": 1, "b": 2, "c": None},
        {"a": None, "b": 3, "c": 4},
    ]


def test_buffer_is_identity():
    assert evaluate("Table.Buffer(Table.FromRecords({[a=1]}))") == [{"a": 1}]


def test_column_count_is_empty_has_columns():
    assert evaluate("Table.ColumnCount(Table.FromRecords({[a=1,b=2]}))") == 2
    assert evaluate("Table.ColumnCount({})") == 0
    assert evaluate("Table.IsEmpty({})") is True
    assert evaluate("Table.IsEmpty(Table.FromRecords({[a=1]}))") is False
    assert (
        evaluate('Table.HasColumns(Table.FromRecords({[a=1,b=2]}), {"a","b"})') is True
    )
    assert (
        evaluate('Table.HasColumns(Table.FromRecords({[a=1,b=2]}), {"a","z"})') is False
    )


def test_transform_column_names_uppercases_every_header():
    query = "Table.TransformColumnNames(Table.FromRecords({[a=1,b=2]}), Text.Upper)"
    assert list(evaluate(query)[0].keys()) == ["A", "B"]


def test_remove_rows_with_errors_is_identity():
    """This evaluator raises a Python exception the instant a formula
    errors, so a table can never contain an error cell to remove - see the
    module docstring/comment for why identity is the correct answer here."""
    query = "Table.RemoveRowsWithErrors(Table.FromRecords({[a=1],[a=2]}))"
    assert evaluate(query) == [{"a": 1}, {"a": 2}]


def test_select_duplicates_keeps_every_row_in_a_group_of_two_or_more():
    query = """
    Table.SelectDuplicates(
        Table.FromRecords({[a=1],[a=2],[a=1],[a=3],[a=1]})
    )
    """
    assert [r["a"] for r in evaluate(query)] == [1, 1, 1]


def test_select_duplicates_with_criteria_columns():
    query = """
    Table.SelectDuplicates(
        Table.FromRecords({[a=1,b=1],[a=1,b=2],[a=2,b=3]}),
        {"a"}
    )
    """
    assert evaluate(query) == [{"a": 1, "b": 1}, {"a": 1, "b": 2}]


def test_table_max_matches_the_microsoft_docs_examples():
    assert evaluate('Table.Max(Table.FromRecords({[a=2,b=4],[a=6,b=8]}), "a")') == {
        "a": 6,
        "b": 8,
    }
    assert evaluate('Table.Max(Table.FromRecords({}), "a", -1)') == -1


def test_table_min_basic():
    assert evaluate('Table.Min(Table.FromRecords({[a=2,b=4],[a=6,b=8]}), "a")') == {
        "a": 2,
        "b": 4,
    }


def test_table_max_honours_a_descending_direction():
    """A Descending direction on the criteria flips which real value counts
    as "biggest" for Max, mirroring Table.Sort's own semantics."""
    query = 'Table.Max(Table.FromRecords({[a=2],[a=6]}), {{"a", Order.Descending}})'
    assert evaluate(query) == {"a": 2}


# --------------------------------------------------------------------------
# From/To conversions
# --------------------------------------------------------------------------


def test_from_list_default_comma_splitter_matches_microsoft_docs_example():
    query = """
    Table.FromList(
        {"a,apple", "b,ball", "c,cookie", "d,door"},
        null,
        {"Letter", "Example Word"}
    )
    """
    assert evaluate(query) == [
        {"Letter": "a", "Example Word": "apple"},
        {"Letter": "b", "Example Word": "ball"},
        {"Letter": "c", "Example Word": "cookie"},
        {"Letter": "d", "Example Word": "door"},
    ]


def test_from_list_auto_detects_column_count_when_omitted():
    query = 'Table.FromList({"a,b,c", "x,y"})'
    assert evaluate(query) == [
        {"Column1": "a", "Column2": "b", "Column3": "c"},
        {"Column1": "x", "Column2": "y", "Column3": None},
    ]


def test_from_list_with_a_custom_splitter():
    query = (
        'Table.FromList({"a-apple", "b-ball"}, '
        'Splitter.SplitTextByDelimiter("-"), {"Letter", "Word"})'
    )
    assert evaluate(query) == [
        {"Letter": "a", "Word": "apple"},
        {"Letter": "b", "Word": "ball"},
    ]


def test_to_list_single_column_no_combiner():
    assert evaluate("Table.ToList(Table.FromRecords({[a=1],[a=2],[a=3]}))") == [1, 2, 3]


def test_to_list_multi_column_without_combiner_errors():
    with pytest.raises(EvalError, match="needs a combiner"):
        evaluate("Table.ToList(Table.FromRecords({[a=1,b=2]}))")


def test_to_list_with_a_combiner():
    query = """
    Table.ToList(
        Table.FromRecords({[a=1,b=2],[a=3,b=4]}),
        (vals) => List.Sum(vals)
    )
    """
    assert evaluate(query) == [3, 7]


def test_from_columns_matches_microsoft_docs_example_with_names():
    query = """
    Table.FromColumns(
        {
            {1, 2, 3},
            {"Bob", "Jim", "Paul"},
            {"123-4567", "987-6543", "543-7890"}
        },
        {"CustomerID", "Name", "Phone"}
    )
    """
    assert evaluate(query) == [
        {"CustomerID": 1, "Name": "Bob", "Phone": "123-4567"},
        {"CustomerID": 2, "Name": "Jim", "Phone": "987-6543"},
        {"CustomerID": 3, "Name": "Paul", "Phone": "543-7890"},
    ]


def test_from_columns_ragged_columns_pad_with_null():
    """Docs Example 3: differing column-list lengths pad the shorter ones
    with null out to the tallest column's height."""
    query = """
    Table.FromColumns(
        {{1, 2, 3}, {4, 5}, {6, 7, 8, 9}},
        {"column1", "column2", "column3"}
    )
    """
    assert evaluate(query) == [
        {"column1": 1, "column2": 4, "column3": 6},
        {"column1": 2, "column2": 5, "column3": 7},
        {"column1": 3, "column2": None, "column3": 8},
        {"column1": None, "column2": None, "column3": 9},
    ]


def test_to_columns_is_the_inverse_of_from_columns():
    query = 'Table.ToColumns(Table.FromRecords({[a=1,b="x"],[a=2,b="y"]}))'
    assert evaluate(query) == [[1, 2], ["x", "y"]]


def test_from_rows_and_to_rows_match_microsoft_docs_examples():
    to_rows = """
    Table.ToRows(
        Table.FromRecords({
            [CustomerID = 1, Name = "Bob", Phone = "123-4567"],
            [CustomerID = 2, Name = "Jim", Phone = "987-6543"]
        })
    )
    """
    assert evaluate(to_rows) == [[1, "Bob", "123-4567"], [2, "Jim", "987-6543"]]

    from_rows = """
    Table.FromRows(
        {{1, "Bob", "123-4567"}, {2, "Jim", "987-6543"}},
        {"CustomerID", "Name", "Phone"}
    )
    """
    assert evaluate(from_rows) == [
        {"CustomerID": 1, "Name": "Bob", "Phone": "123-4567"},
        {"CustomerID": 2, "Name": "Jim", "Phone": "987-6543"},
    ]


def test_from_rows_without_columns_uses_generic_names():
    query = 'Table.FromRows({{1, "a"}, {2, "b"}})'
    assert evaluate(query) == [
        {"Column1": 1, "Column2": "a"},
        {"Column1": 2, "Column2": "b"},
    ]


def test_from_value_matches_microsoft_docs_examples():
    assert evaluate("Table.FromValue(1)") == [{"Value": 1}]
    assert evaluate('Table.FromValue({1, "Bob", "123-4567"})') == [
        {"Value": 1},
        {"Value": "Bob"},
        {"Value": "123-4567"},
    ]
    assert evaluate('Table.FromValue(1, [DefaultColumnName = "MyValue"])') == [
        {"MyValue": 1}
    ]


def test_from_value_of_a_record_becomes_a_one_row_table_of_its_fields():
    assert evaluate("Table.FromValue([a=1,b=2])") == [{"a": 1, "b": 2}]


def test_from_value_of_a_list_of_records_is_identity():
    """This tool's data model has no separate table/list-of-records type
    tag (see the module docstring), so a list already shaped like a table
    is treated as one - the same modelling choice the rest of the codebase
    already makes (Table.FromRecords/ToRecords)."""
    query = "Table.FromValue({[a=1],[a=2]})"
    assert evaluate(query) == [{"a": 1}, {"a": 2}]


def test_from_value_rejects_an_unknown_option():
    with pytest.raises(UnsupportedError, match="option"):
        evaluate("Table.FromValue(1, [Bogus = true])")
