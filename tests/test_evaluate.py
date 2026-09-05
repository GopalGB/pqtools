import math

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


def test_division_by_zero_follows_ieee_754_rather_than_raising():
    """`8 / 0 // #infinity` and `0 / 0 // #nan` are the spec's own examples.

    pqtools raised here until 0.10.0, so `[Total] / [Count]` failed on a zero
    count where Power Query returns #infinity - a query that runs there and
    breaks here, which is the divergence this package exists to prevent.
    """
    assert evaluate("8 / 0") == math.inf
    assert evaluate("-8 / 0") == -math.inf
    assert math.isnan(evaluate("0 / 0"))
    assert evaluate("0 / null") is None


def test_ampersand_rejects_a_kind_it_is_not_defined_for():
    # `&` is defined over text, list, record, table and date-with-time. A
    # number is on none of those rows.
    with pytest.raises(EvalError, match="operator & is not defined"):
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
    # `let A = B, B = A in A` is a real cycle. `let A = A in A` is NOT one
    # and used to be the case pinned here: M's plain identifier is the
    # EXCLUSIVE form, so the `A` on the right skips the `A` being defined
    # and looks outward - "unknown identifier: A", the same answer Power
    # Query gives. `@A` is the spelling that means the binding itself.
    with pytest.raises(EvalError, match="circular reference in 'A'"):
        evaluate("let A = B, B = A in A")
    with pytest.raises(UnsupportedError, match="unknown identifier: A"):
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
    # `1 / 0` used to stand in for "a runtime error" here. It is not one:
    # M returns #infinity, so `try 1 / 0 otherwise -1` is #infinity in Power
    # Query too. A type mismatch is a real error.
    assert evaluate('try "a" + 1 otherwise -1') == -1
    assert evaluate("try 1 + 1 otherwise -1") == 2


def test_try_otherwise_never_swallows_unsupported_error():
    with pytest.raises(UnsupportedError):
        evaluate("try SharePoint.Files(1) otherwise -1")


def test_try_otherwise_never_swallows_a_policy_block():
    # The dangerous version of the same bug: if `otherwise` catches a blocked
    # connector, the caller silently receives the fallback value and never
    # learns the fetch did not happen.
    from pqtools.io import IOBlockedError

    with pytest.raises(IOBlockedError):
        evaluate('try Web.Contents("https://example.com") otherwise -1')


def test_bare_try_returns_the_error_record():
    # Was refused until 0.9.0. `try x` is a value in M - the record that says
    # whether x failed - and without it every caller has to invent a sentinel.
    assert evaluate("try 1 + 1") == {"HasError": False, "Value": 2}


def test_try_catch_runs_the_handler_with_the_error():
    assert evaluate('try ("a" + 1) catch (e) => e[Reason]') == "Expression.Error"


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


@pytest.mark.parametrize("name", ["SharePoint.Files", "SharePoint.Tables"])
def test_auth_bound_connectors_still_name_the_host(name):
    # Web.Contents, Sql.Database, Excel.Workbook, Folder.Files and the DB
    # family left this list in 0.9.0: they are implemented natively now
    # (builtins/_sources.py) behind a permission gate. SharePoint remains,
    # because completing its OAuth flow would mean holding the user's tokens.
    with pytest.raises(UnsupportedError, match="Fabric or PQTest"):
        evaluate(f"{name}(1)")


def test_hash_shared_names_what_it_needs():
    with pytest.raises(UnsupportedError, match="section document"):
        evaluate("#shared")


def test_meta_expression_is_unsupported():
    with pytest.raises(UnsupportedError, match="meta"):
        evaluate("1 meta [a = 1]")


def test_null_coalescing_returns_the_right_side_only_for_null():
    assert evaluate("null ?? 1") == 1
    assert evaluate("2 ?? 1") == 2
    assert evaluate("null ?? null") is None
    # Short-circuits: a failing right side is never reached when the left
    # side is non-null, which is the whole point of using `??` as a guard.
    assert evaluate('2 ?? (1 + "a")') == 2


def test_as_checks_a_type_and_does_not_convert():
    """ "Is compatible primitive/nullable primitive type or error."

    The trap is reading `as` as a cast: `"1" as number` is an error in M,
    not 1. Converting here would silently accept data that Power Query
    rejects.
    """
    assert evaluate("1 as number") == 1
    assert evaluate("null as nullable number") is None
    with pytest.raises(EvalError, match="does not conform"):
        evaluate('"1" as number')
    with pytest.raises(EvalError, match="does not conform"):
        evaluate("null as number")


def test_is_tests_conformance_to_a_primitive_type():
    assert evaluate("1 is number") is True
    assert evaluate('"a" is number') is False
    assert evaluate("null is number") is False
    assert evaluate("null is nullable number") is True
    assert evaluate("1 is any") is True
    assert evaluate("[a = 1] is record") is True
    assert evaluate("{1, 2} is list") is True
    assert evaluate("{1, 2} is table") is False


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


def test_parameter_type_ascription_is_checked_not_ignored():
    # Accepting the declaration and then not enforcing it would be worse than
    # refusing it: a type error the author expected to catch becomes a wrong
    # value further down the chain.
    assert evaluate("((x as number) => x)(1)") == 1
    with pytest.raises(EvalError, match="expected number, got text"):
        evaluate('((x as number) => x)("a")')


def test_function_return_type_ascription_is_checked_not_ignored():
    assert evaluate("((x) as number => x)(1)") == 1
    with pytest.raises(EvalError, match="return value: expected text"):
        evaluate("((x) as text => x)(1)")


def test_field_projection_keeps_the_named_fields_in_the_order_named():
    assert evaluate("[a = 1, b = 2][[a]]") == {"a": 1}
    assert evaluate("[a = 1, b = 2, c = 3][[c], [a]]") == {"c": 3, "a": 1}
    # On a table the same syntax is column selection, which is the same
    # operation applied row-wise.
    table = '#table({"x", "y"}, {{1, 2}, {3, 4}})'
    assert evaluate(f"{table}[[y]]") == [{"y": 2}, {"y": 4}]
    with pytest.raises(EvalError, match="field not found"):
        evaluate("[a = 1][[zzz]]")
    assert evaluate("[a = 1][[zzz]]?") == {"zzz": None}


def test_unknown_identifier_is_unsupported():
    with pytest.raises(UnsupportedError, match="unknown identifier: Nope"):
        evaluate("Nope")


def test_unknown_builtin_style_identifier_is_unsupported():
    """A name that is not in the registry must say so, not resolve to null.

    The example used to be a connector, on the reasoning that connectors were
    permanently out of scope. 0.9.0 implemented them, so that choice broke -
    twice now, since it had already broken once when Table.AddIndexColumn was
    implemented. A fictional namespace cannot be overtaken by a later release.
    """
    with pytest.raises(UnsupportedError, match="unknown identifier"):
        evaluate("Nonexistent.Function(1)")


def test_outer_scope_at_identifier_resolves_the_enclosing_binding():
    # `@name` exists to name the enclosing binding when a record field would
    # shadow it. Scopes here are already lexical, so the two spellings agree.
    source = "let f = (n) => if n <= 1 then 1 else n * @f(n - 1) in f(5)"
    assert evaluate(source) == 120


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


def test_text_from_culture_argument():
    # This used to pin `Text.From(1, "en-US")` as an arity error ("Text.From
    # with 2 argument(s)") - that was itself the bug: Text.From's own Syntax
    # (learn.microsoft.com/en-us/powerquery-m/text-from) documents
    # `optional culture as nullable text` as a real second argument, so
    # rejecting it outright was refusing a documented call shape, not
    # protecting against an undocumented one. An invariant-equivalent
    # culture is now accepted (and has no effect on a plain number); a real
    # non-invariant culture still refuses by name - Text.From's own
    # Example 3 uses "de-DE" for exactly this reason - which is the
    # boundary this test now actually pins.
    assert evaluate('Text.From(1, "en-US")') == "1"
    with pytest.raises(UnsupportedError, match="de-DE"):
        evaluate('Text.From(1, "de-DE")')


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
    # Not 0. "Returns null if there are no non-null values in the list" is
    # List.Sum's own About text; 0 is Python's convention for an empty sum,
    # and this line asserted it for two releases because the author reached
    # for the language they were writing in rather than the one being
    # modelled. Downstream that matters: null propagates through the next
    # arithmetic step, 0 silently does not.
    assert evaluate("List.Sum({})") is None
    assert evaluate("List.Sum({null, null})") is None
    assert evaluate("List.Sum({1, null, 3})") == 4
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
