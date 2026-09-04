import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Literals, arithmetic, comparisons, logical, text &
# --------------------------------------------------------------------------


def test_numeric_text_logical_null_literals():
    assert evaluate("1") == 1
    assert evaluate("1.5") == 1.5
    assert evaluate('"hello"') == "hello"
    assert evaluate('"a""b"') == 'a"b'
    assert evaluate("true") is True
    assert evaluate("false") is False
    assert evaluate("null") is None
    assert evaluate("0x1A") == 26


def test_arithmetic_operators():
    assert evaluate("1 + 2") == 3
    assert evaluate("5 - 2") == 3
    assert evaluate("3 * 4") == 12
    assert evaluate("7 / 2") == 3.5
    assert evaluate('"a" & "b"') == "ab"


def test_division_by_zero_is_eval_error():
    with pytest.raises(EvalError, match="division by zero"):
        evaluate("1 / 0")


def test_ampersand_requires_text_both_sides():
    with pytest.raises(EvalError, match="& requires text"):
        evaluate('1 & "a"')


def test_comparison_operators():
    assert evaluate("1 = 1") is True
    assert evaluate("1 <> 2") is True
    assert evaluate("1 < 2") is True
    assert evaluate("2 <= 2") is True
    assert evaluate("3 > 2") is True
    assert evaluate("3 >= 3") is True
    assert evaluate('"a" < "b"') is True


def test_relational_rejects_mismatched_types():
    with pytest.raises(EvalError, match="relational operators require"):
        evaluate('1 < "a"')


def test_equality_across_types_is_false_not_an_error():
    assert evaluate('1 = "1"') is False
    assert evaluate("null = null") is True
    assert evaluate("1 = null") is False


def test_logical_operators_short_circuit():
    assert evaluate("true and false") is False
    assert evaluate("false and (1/0 = 1)") is False
    assert evaluate("true or (1/0 = 1)") is True
    assert evaluate("not true") is False


def test_unary_operators():
    assert evaluate("-5") == -5
    assert evaluate("+5") == 5
    assert evaluate("- -5") == 5
    assert evaluate("not not true") is True


# --------------------------------------------------------------------------
# let/in - laziness, memoisation, shadowing
# --------------------------------------------------------------------------


def test_let_basic_and_shadowing():
    assert evaluate("let x = 1, y = x + 1 in y") == 2
    assert evaluate("let x = 1 in let x = 2 in x") == 2


def test_let_bindings_can_reference_each_other_regardless_of_order():
    assert evaluate("let B = A, A = 1 in B") == 1


def test_let_is_lazy_unused_binding_never_evaluated():
    # Web.Contents is unimplemented and would raise if evaluated.
    assert evaluate('let Unused = Web.Contents("x"), Used = 1 in Used') == 1


def test_let_binding_is_memoised_not_recomputed():
    source = "let Counter = List.Count({1,2,3}), Total = Counter + Counter in Total"
    assert evaluate(source) == 6


def test_let_circular_reference_is_eval_error():
    with pytest.raises(EvalError, match="circular"):
        evaluate("let A = A in A")


# --------------------------------------------------------------------------
# records and lists
# --------------------------------------------------------------------------


def test_record_literal_and_field_access():
    assert evaluate("[a = 1, b = 2]") == {"a": 1, "b": 2}
    assert evaluate("[a = 1][a]") == 1


def test_record_field_access_missing_field_errors():
    with pytest.raises(EvalError, match="field not found: b"):
        evaluate("[a = 1][b]")


def test_record_field_access_optional_returns_null():
    assert evaluate("[a = 1][b]?") is None


def test_list_literal_and_index():
    assert evaluate("{1, 2, 3}") == [1, 2, 3]
    assert evaluate("{1, 2, 3}{1}") == 2


def test_list_index_out_of_range_errors_unless_optional():
    with pytest.raises(EvalError, match="out of range"):
        evaluate("{1, 2}{5}")
    assert evaluate("{1, 2}{5}?") is None


# --------------------------------------------------------------------------
# if / lambdas / try-otherwise
# --------------------------------------------------------------------------


def test_if_then_else():
    assert evaluate('if 1 = 1 then "yes" else "no"') == "yes"
    assert evaluate('if 1 = 2 then "yes" else "no"') == "no"


def test_if_condition_must_be_logical():
    with pytest.raises(EvalError, match="if condition must be logical"):
        evaluate("if 1 then 2 else 3")


def test_lambda_call_and_each():
    assert evaluate("((x, y) => x + y)(1, 2)") == 3
    assert evaluate("List.Transform({1,2,3}, each _ * 2)") == [2, 4, 6]


def test_lambda_wrong_arity_is_eval_error():
    with pytest.raises(EvalError, match="expects 2 argument"):
        evaluate("((x, y) => x + y)(1)")


def test_try_otherwise_recovers_from_runtime_error():
    assert evaluate("try 1 / 0 otherwise -1") == -1
    assert evaluate("try 1 + 1 otherwise -1") == 2


def test_try_otherwise_never_swallows_unsupported_error():
    with pytest.raises(UnsupportedError):
        evaluate('try Web.Contents("x") otherwise -1')


def test_try_without_otherwise_is_unsupported():
    with pytest.raises(UnsupportedError, match="try without otherwise"):
        evaluate("try 1 / 0")


def test_try_catch_is_unsupported():
    with pytest.raises(UnsupportedError, match="catch"):
        evaluate("try 1 / 0 catch (e) => 0")


def test_implicit_field_shorthand_outside_each_errors():
    with pytest.raises(EvalError, match="outside of an each"):
        evaluate("[a]")


# --------------------------------------------------------------------------
# bindings - the whole point
# --------------------------------------------------------------------------


def test_bind_replaces_a_let_binding_without_evaluating_it():
    source = (
        'let Source = Csv.Document(File.Contents("ignored.csv")), '
        'Kept = Table.SelectRows(Source, each [b] <> "y"), '
        'Renamed = Table.RenameColumns(Kept, {{"a", "id"}}) '
        "in Renamed"
    )
    table = [
        {"a": "1", "b": "x"},
        {"a": "2", "b": "y"},
        {"a": "3", "b": "z"},
    ]
    result = evaluate(source, bindings={"Source": table})
    assert result == [{"id": "1", "b": "x"}, {"id": "3", "b": "z"}]


def test_without_bind_a_missing_local_file_names_the_path_and_bind():
    # Behaviour changed when local-file connectors landed: this is no longer
    # refused as a connector, it is attempted. A real query carries the
    # authoring machine's path, so the error has to name both the path that
    # is missing and --bind as the way forward.
    source = 'let Source = Csv.Document(File.Contents("ignored.csv")) in Source'
    with pytest.raises(EvalError) as excinfo:
        evaluate(source)
    message = str(excinfo.value)
    assert "ignored.csv" in message
    assert "--bind" in message


def test_a_local_csv_source_runs_with_no_bind_at_all(tmp_path):
    csv = tmp_path / "s.csv"
    csv.write_text("a,b\n1,x\n", encoding="utf-8")
    source = (
        f'let Source = Csv.Document(File.Contents("{csv.as_posix()}")), '
        "Promoted = Table.PromoteHeaders(Source) in Promoted"
    )
    assert evaluate(source) == [{"a": "1", "b": "x"}]


def test_bind_prepopulates_top_level_scope_even_without_a_let():
    assert evaluate("Table.RowCount(Source)", bindings={"Source": [{"a": 1}]}) == 1


# --------------------------------------------------------------------------
# the honest boundary - connectors, #shared, meta, type ascription, unknowns
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Web.Contents",
        "Sql.Database",
        "Excel.Workbook",
        "SharePoint.Files",
        "Odbc.DataSource",
        "PostgreSQL.Database",
        "Folder.Files",
        "Binary.FromText",
    ],
)
def test_connectors_raise_unsupported_naming_the_fabric_or_pqtest_host(name):
    # File.Contents and Csv.Document deliberately left this list: they are
    # implemented natively now (builtins/_connectors.py). What remains needs
    # credentials, a network identity, or query folding into a remote engine -
    # things no local approximation can honestly stand in for.
    with pytest.raises(UnsupportedError, match="Fabric or PQTest"):
        evaluate(f"{name}(1)")


def test_hash_shared_is_unsupported():
    with pytest.raises(UnsupportedError, match="#shared"):
        evaluate("#shared")


def test_meta_expression_is_unsupported():
    with pytest.raises(UnsupportedError, match="meta"):
        evaluate("1 meta [a = 1]")


def test_null_coalescing_is_unsupported():
    with pytest.raises(UnsupportedError, match="null-coalescing"):
        evaluate("null ?? 1")


def test_as_expression_type_ascription_is_unsupported():
    with pytest.raises(UnsupportedError, match="type ascription"):
        evaluate("1 as number")


def test_is_expression_is_unsupported():
    with pytest.raises(UnsupportedError, match="is-expression"):
        evaluate("1 is number")


def test_type_value_evaluates_to_a_type():
    """0.5.0 implemented the type system; `type text` is a real value now.

    This test previously asserted the opposite. A test that pins a limitation
    becomes a test that resists the fix, so it asserts the capability instead of
    being deleted - deleting it would have removed the only coverage of bare
    `type` evaluation.
    """
    from pqtools.builtins._type import _MType

    assert isinstance(evaluate("type text"), _MType)
    assert isinstance(evaluate("Int64.Type"), _MType)
    assert evaluate("type text") is not evaluate("type number")


def test_parameter_type_ascription_is_unsupported():
    with pytest.raises(UnsupportedError, match="type ascription"):
        evaluate("((x as number) => x)(1)")


def test_function_return_type_ascription_is_unsupported():
    with pytest.raises(UnsupportedError, match="type ascription"):
        evaluate("((x) as number => x)(1)")


def test_field_projection_is_unsupported():
    with pytest.raises(UnsupportedError, match="projection"):
        evaluate("[a = 1, b = 2][[a]]")


def test_unknown_identifier_is_unsupported():
    with pytest.raises(UnsupportedError, match="unknown identifier: Nope"):
        evaluate("Nope")


def test_unknown_builtin_style_identifier_is_unsupported():
    """Uses a CONNECTOR as the example of a name that will never resolve.

    This previously used `Table.AddIndexColumn`, which 0.5.0 implemented - so the
    test broke for the good reason that the gap it relied on had closed. A
    connector is the durable choice: running one needs Microsoft's Mashup Engine,
    so it is out of scope permanently by design, not merely unimplemented yet.
    """
    with pytest.raises(UnsupportedError, match="Sql.Database"):
        evaluate('Sql.Database("server", "db")')


def test_outer_scope_at_identifier_is_unsupported():
    # Lazy, so the binding must actually be forced (referenced from the
    # body) for the @A inside it to ever be evaluated.
    with pytest.raises(UnsupportedError, match="outer-scope"):
        evaluate("let A = @A in A")


def test_section_document_points_at_member_flag():
    with pytest.raises(UnsupportedError, match="--member"):
        evaluate("section Section1; shared A = 1;")


def test_max_steps_budget_is_enforced():
    with pytest.raises(EvalError, match="max_steps"):
        evaluate("1 + 1 + 1 + 1 + 1", max_steps=2)


# --------------------------------------------------------------------------
# Text.*
# --------------------------------------------------------------------------


def test_text_builtins():
    assert evaluate("Text.From(42)") == "42"
    assert evaluate("Text.From(true)") == "TRUE"
    assert evaluate("Text.From(null)") is None
    assert evaluate('Text.Upper("ab")') == "AB"
    assert evaluate('Text.Lower("AB")') == "ab"
    assert evaluate('Text.Length("abc")') == 3
    assert evaluate('Text.Combine({"a","b","c"}, "-")') == "a-b-c"
    assert evaluate('Text.Combine({"a","b"})') == "ab"
    assert evaluate('Text.Contains("abc", "b")') is True
    assert evaluate('Text.Replace("abc", "b", "x")') == "axc"
    assert evaluate('Text.Split("a,b,c", ",")') == ["a", "b", "c"]
    assert evaluate('Text.Start("abcdef", 3)') == "abc"
    assert evaluate('Text.End("abcdef", 3)') == "def"
    assert evaluate('Text.End("abcdef", 0)') == ""
    assert evaluate('Text.Trim("  hi  ")') == "hi"
    assert evaluate('Text.Trim("xxhixx", "x")') == "hi"


def test_text_from_unsupported_extra_argument():
    with pytest.raises(UnsupportedError, match="Text.From with 2"):
        evaluate('Text.From(1, "en-US")')


# --------------------------------------------------------------------------
# Number.*
# --------------------------------------------------------------------------


def test_number_builtins():
    assert evaluate('Number.From("42")') == 42
    assert evaluate("Number.From(true)") == 1
    assert evaluate("Number.Round(2.4)") == 2
    assert (
        evaluate("Number.Round(2.345, 2)") == 2.35
        or evaluate("Number.Round(2.345, 2)") == 2.34
    )  # float representation - either is a faithful round-half-to-even
    assert evaluate("Number.Abs(-5)") == 5


# --------------------------------------------------------------------------
# List.*
# --------------------------------------------------------------------------


def test_list_builtins():
    assert evaluate("List.Count({1,2,3})") == 3
    assert evaluate("List.Sum({1,2,3})") == 6
    assert evaluate("List.Sum({})") == 0
    assert evaluate("List.Max({1,5,3})") == 5
    assert evaluate("List.Max({}, 0)") == 0
    assert evaluate("List.Max({})") is None
    assert evaluate("List.Min({1,5,3})") == 1
    assert evaluate("List.Average({2,4})") == 3
    assert evaluate("List.Average({})") is None
    assert evaluate("List.Select({1,2,3,4}, each _ > 2)") == [3, 4]
    assert evaluate("List.First({1,2,3})") == 1
    assert evaluate("List.First({}, -1)") == -1
    assert evaluate("List.First({})") is None
    assert evaluate("List.Last({1,2,3})") == 3
    assert evaluate("List.Reverse({1,2,3})") == [3, 2, 1]
    assert evaluate("List.Sort({3,1,2})") == [1, 2, 3]
    assert evaluate("List.Contains({1,2,3}, 2)") is True
    assert evaluate("List.Distinct({1,1,2,2,3})") == [1, 2, 3]
    assert evaluate("List.Range({1,2,3,4,5}, 1)") == [2, 3, 4, 5]
    assert evaluate("List.Range({1,2,3,4,5}, 1, 2)") == [2, 3]


def test_list_select_predicate_must_be_logical():
    with pytest.raises(EvalError, match="predicate must return a logical"):
        evaluate("List.Select({1,2}, each _)")


def test_list_sort_incomparable_values_error():
    with pytest.raises(EvalError, match="not comparable"):
        evaluate('List.Sort({1, "a"})')


# --------------------------------------------------------------------------
# Record.*
# --------------------------------------------------------------------------


def test_record_builtins():
    assert evaluate('Record.Field([a = 1], "a")') == 1
    assert evaluate("Record.FieldNames([a = 1, b = 2])") == ["a", "b"]
    assert evaluate('Record.HasFields([a = 1], "a")') is True
    assert evaluate('Record.HasFields([a = 1], {"a", "b"})') is False
    assert evaluate('Record.AddField([a = 1], "b", 2)') == {"a": 1, "b": 2}
    assert evaluate('Record.RemoveFields([a = 1, b = 2], "b")') == {"a": 1}


def test_record_add_field_existing_errors():
    with pytest.raises(EvalError, match="already exists"):
        evaluate('Record.AddField([a = 1], "a", 2)')


def test_record_remove_fields_missing_errors():
    with pytest.raises(EvalError, match="no such field"):
        evaluate('Record.RemoveFields([a = 1], "b")')


# --------------------------------------------------------------------------
# Table.*
# --------------------------------------------------------------------------

_TABLE = "{[a = 1, b = 10], [a = 2, b = 20], [a = 3, b = 30]}"


def test_table_builtins():
    assert evaluate(f"Table.FromRecords({_TABLE})") == [
        {"a": 1, "b": 10},
        {"a": 2, "b": 20},
        {"a": 3, "b": 30},
    ]
    assert evaluate(f"Table.ToRecords({_TABLE})")[0] == {"a": 1, "b": 10}
    assert evaluate(f"Table.RowCount({_TABLE})") == 3
    assert evaluate(f"Table.ColumnNames({_TABLE})") == ["a", "b"]
    assert evaluate("Table.ColumnNames({})") == []
    assert evaluate(f"Table.SelectRows({_TABLE}, each [a] > 1)") == [
        {"a": 2, "b": 20},
        {"a": 3, "b": 30},
    ]
    assert evaluate(f'Table.SelectColumns({_TABLE}, "a")') == [
        {"a": 1},
        {"a": 2},
        {"a": 3},
    ]
    assert evaluate(f'Table.RemoveColumns({_TABLE}, "b")') == [
        {"a": 1},
        {"a": 2},
        {"a": 3},
    ]
    assert evaluate(f'Table.RenameColumns({_TABLE}, {{"a", "id"}})')[0] == {
        "id": 1,
        "b": 10,
    }
    assert evaluate(f'Table.AddColumn({_TABLE}, "c", each [a] + [b])')[0] == {
        "a": 1,
        "b": 10,
        "c": 11,
    }
    assert evaluate(f'Table.TransformColumns({_TABLE}, {{"a", each _ * 10}})')[0] == {
        "a": 10,
        "b": 10,
    }
    assert evaluate(f'Table.Sort({_TABLE}, "a")')[0]["a"] == 1
    assert evaluate(f"Table.FirstN({_TABLE}, 2)") == [
        {"a": 1, "b": 10},
        {"a": 2, "b": 20},
    ]
    assert evaluate(f"Table.LastN({_TABLE}, 1)") == [{"a": 3, "b": 30}]
    assert evaluate("Table.Distinct({[a=1],[a=1],[a=2]})") == [{"a": 1}, {"a": 2}]
    assert evaluate('Table.Distinct({[a=1,b=1],[a=1,b=2]}, "a")') == [{"a": 1, "b": 1}]


def test_table_select_columns_missing_column_errors():
    with pytest.raises(EvalError, match="no such column: c"):
        evaluate(f'Table.SelectColumns({_TABLE}, "c")')


def test_table_add_column_existing_errors():
    with pytest.raises(EvalError, match="already exists"):
        evaluate(f'Table.AddColumn({_TABLE}, "a", each 1)')


def test_table_sort_descending_via_the_order_enum():
    """1 IS Order.Descending's value, so this sorts rather than being refused."""
    rows = evaluate(f'Table.Sort({_TABLE}, {{{{"a", 1}}}})')
    values = [row["a"] for row in rows]
    assert values == sorted(values, reverse=True)


def test_table_sort_rejects_a_direction_that_is_not_the_order_enum():
    with pytest.raises(UnsupportedError, match="Order.Ascending or Order.Descending"):
        evaluate(f'Table.Sort({_TABLE}, {{{{"a", 7}}}})')


# --------------------------------------------------------------------------
# Json.Document / Logical.From
# --------------------------------------------------------------------------


def test_json_document_parses_text_only():
    assert evaluate('Json.Document("[1,2,3]")') == [1, 2, 3]
    assert evaluate('Json.Document("{""a"":1}")') == {"a": 1}


def test_json_document_invalid_text_is_eval_error():
    with pytest.raises(EvalError, match="invalid JSON"):
        evaluate('Json.Document("not json")')


def test_logical_from():
    assert evaluate('Logical.From("true")') is True
    assert evaluate('Logical.From("FALSE")') is False
    assert evaluate("Logical.From(0)") is False
    assert evaluate("Logical.From(1)") is True


def test_logical_from_invalid_text_is_eval_error():
    with pytest.raises(EvalError, match="not a logical value"):
        evaluate('Logical.From("maybe")')


def test_table_sort_accepts_the_shapes_power_query_emits():
    """Power Query's UI writes {{"Col", Order.Descending}}; that form must work."""
    rows = 'Table.FromRecords({[a=2,b="x"],[a=1,b="y"],[a=2,b="a"]})'
    # bare column name
    assert [r["a"] for r in evaluate(f'Table.Sort({rows}, "a")')] == [1, 2, 2]
    # list of names
    assert [r["b"] for r in evaluate(f'Table.Sort({rows}, {{"a", "b"}})')] == [
        "y",
        "a",
        "x",
    ]
    # the generated form, explicit ascending
    assert [
        r["a"] for r in evaluate(f'Table.Sort({rows}, {{{{"a", Order.Ascending}}}})')
    ] == [
        1,
        2,
        2,
    ]
    # descending
    assert [
        r["a"] for r in evaluate(f'Table.Sort({rows}, {{{{"a", Order.Descending}}}})')
    ] == [
        2,
        2,
        1,
    ]
    # mixed directions, stable across keys
    mixed = (
        f'Table.Sort({rows}, {{{{"a", Order.Descending}}, {{"b", Order.Ascending}}}})'
    )
    assert [(r["a"], r["b"]) for r in evaluate(mixed)] == [(2, "a"), (2, "x"), (1, "y")]


def test_order_enum_resolves_but_a_bad_direction_is_refused():
    assert evaluate("Order.Ascending") == 0
    assert evaluate("Order.Descending") == 1
    with pytest.raises(UnsupportedError, match="Order.Ascending or Order.Descending"):
        evaluate('Table.Sort(Table.FromRecords({[a=1]}), {{"a", 7}})')
