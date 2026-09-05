"""Tests for the ``Type.*``/``Value.*`` batch added to ``builtins/_type.py``:

``Type.ForRecord``, ``Type.RecordFields``, ``Type.ClosedRecord``,
``Type.OpenRecord``, ``Type.IsOpenRecord``, ``Type.ForFunction``,
``Type.FunctionParameters``, ``Type.FunctionRequiredParameters``,
``Type.FunctionReturn``, ``Type.AddTableKey``, ``Type.TableKeys``,
``Type.ReplaceTableKeys``, ``Type.TablePartitionKey``,
``Type.ReplaceTablePartitionKey``, ``Type.TableColumn``, ``Type.TableRow``,
``Type.TableSchema``, ``Type.Facets``, ``Type.ReplaceFacets``,
``Type.Union``, ``Type.ListItem``, ``Value.Alternates``,
``Value.Expression``, ``Value.ReplaceType``, ``Value.VersionIdentity``,
``Value.Versions``.

Every test below exercises real M source through ``evaluate()`` - no test
reaches into ``_MType`` directly - because the whole point of this batch is
whether the M-visible functions answer correctly, not whether the Python
dataclass looks right in isolation. Where a function's own Microsoft
example cannot run in this evaluator at all (a bare ``type [...]`` /
``type function (...) as ...`` / ``type {...}`` literal, or a ``type table
rowType`` identifier reference - none of which this parser constructs; see
``builtins/_type.py``'s module docstring and the batch report), the
corresponding behaviour is exercised here through composition instead
(``Type.ForRecord``/``Type.ForFunction``, or a real ``type table [...]``
literal), which every one of these functions is fully able to consume.
"""

from __future__ import annotations

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Type.ForRecord / Type.RecordFields
# --------------------------------------------------------------------------


def test_for_record_round_trips_through_record_fields():
    # Mirrors the doc's own worked example for Type.RecordFields
    # (`type [A = number, optional B = any]` -> `[A = [Type = type number,
    # Optional = false], B = [Type = type any, Optional = true]]`), built
    # through Type.ForRecord instead of the bare record-type literal that
    # syntax cannot parse in this evaluator.
    result = evaluate(
        "Type.RecordFields(Type.ForRecord("
        "[A = [Type = type number, Optional = false], "
        "B = [Type = type any, Optional = true]], false))"
    )
    assert result == {
        "A": {"Type": evaluate("type number"), "Optional": False},
        "B": {"Type": evaluate("type any"), "Optional": True},
    }


def test_for_record_preserves_field_declaration_order():
    # Order matters: Type.RecordFields must report fields in the order they
    # were declared, not sorted or reversed - a caller iterating the result
    # (`Record.FieldNames`, `Record.ToList`) would otherwise silently get
    # the wrong column-to-value pairing downstream.
    result = evaluate(
        "Record.FieldNames(Type.RecordFields(Type.ForRecord("
        "[Z = [Type = type text, Optional = false], "
        "A = [Type = type number, Optional = false]], false)))"
    )
    assert result == ["Z", "A"]


def test_for_record_rejects_a_malformed_field_spec():
    with pytest.raises(EvalError, match="Type.ForRecord"):
        evaluate("Type.ForRecord([A = [Type = type number]], false)")


def test_for_record_rejects_a_non_type_value_for_type():
    with pytest.raises(EvalError, match="Type.ForRecord"):
        evaluate('Type.ForRecord([A = [Type = "not a type", Optional = false]], false)')


def test_record_fields_refuses_a_non_record_type():
    # The docs say plainly this is for a RECORD type; a table type or a
    # primitive is a genuine type mismatch, not a feature gap.
    with pytest.raises(EvalError, match="record type"):
        evaluate("Type.RecordFields(type number)")
    with pytest.raises(EvalError, match="record type"):
        evaluate("Type.RecordFields(type table [A = text])")


# --------------------------------------------------------------------------
# Type.ClosedRecord / Type.OpenRecord / Type.IsOpenRecord
# --------------------------------------------------------------------------


_A_RECORD_TYPE = "Type.ForRecord([A = [Type = type number, Optional = false]], {})"


def test_closed_and_open_record_round_trip():
    # Mirrors the doc examples' intent (`type [A = number, ...]` opened/
    # closed) via Type.ForRecord, since the bare literal cannot parse here.
    closed = evaluate(
        f"Type.IsOpenRecord(Type.ClosedRecord({_A_RECORD_TYPE.format('true')}))"
    )
    assert closed is False
    opened = evaluate(
        f"Type.IsOpenRecord(Type.OpenRecord({_A_RECORD_TYPE.format('false')}))"
    )
    assert opened is True


def test_closed_record_on_an_already_closed_record_is_a_no_op():
    result = evaluate(f"Type.ClosedRecord({_A_RECORD_TYPE.format('false')})")
    assert result == evaluate(_A_RECORD_TYPE.format("false"))


def test_open_and_closed_record_reject_non_record_types():
    with pytest.raises(EvalError, match="record type"):
        evaluate("Type.OpenRecord(type number)")
    with pytest.raises(EvalError, match="record type"):
        evaluate("Type.ClosedRecord(type table [A = text])")
    with pytest.raises(EvalError, match="record type"):
        evaluate("Type.IsOpenRecord(type any)")


# --------------------------------------------------------------------------
# Type.ForFunction / Type.FunctionParameters /
# Type.FunctionRequiredParameters / Type.FunctionReturn
# --------------------------------------------------------------------------


def test_for_function_matches_the_doc_example():
    # Type.ForFunction's own doc example needs no type-literal syntax this
    # evaluator lacks, so it runs verbatim.
    result = evaluate(
        "Type.ForFunction("
        "[ReturnType = type number, Parameters = [X = type number]], 1)"
    )
    assert result.kind == "function"


def test_function_parameters_reports_name_to_type_in_order():
    # Mirrors the doc's `type function (x as number, y as text) as any` ->
    # `[x = type number, y = type text]`, built via Type.ForFunction since
    # the bare `type function (...) as ...` literal does not parse here.
    result = evaluate(
        "Type.FunctionParameters(Type.ForFunction("
        "[ReturnType = type any, Parameters = [x = type number, y = type text]], 2))"
    )
    assert result == {"x": evaluate("type number"), "y": evaluate("type text")}


def test_function_required_parameters_matches_min_argument():
    # Mirrors the doc's `(x as number, optional y as text)` -> `1`.
    result = evaluate(
        "Type.FunctionRequiredParameters(Type.ForFunction("
        "[ReturnType = type any, Parameters = [x = type number, y = type text]], 1))"
    )
    assert result == 1


def test_function_return_matches_the_declared_return_type():
    # Mirrors the doc's `type function () as any` -> `type any`.
    result = evaluate(
        "Type.FunctionReturn("
        "Type.ForFunction([ReturnType = type any, Parameters = []], 0))"
    )
    assert result == evaluate("type any")


def test_for_function_rejects_min_outside_the_parameter_count():
    with pytest.raises(EvalError, match="Type.ForFunction"):
        evaluate(
            "Type.ForFunction("
            "[ReturnType = type any, Parameters = [x = type number]], 5)"
        )


def test_function_introspection_rejects_non_function_types():
    with pytest.raises(EvalError, match="function type"):
        evaluate("Type.FunctionParameters(type number)")
    with pytest.raises(EvalError, match="function type"):
        evaluate("Type.FunctionReturn(type table [A = text])")


# --------------------------------------------------------------------------
# Type.AddTableKey / Type.TableKeys / Type.ReplaceTableKeys
# --------------------------------------------------------------------------


def test_add_table_key_then_table_keys_matches_the_doc_example():
    result = evaluate(
        "let\n"
        "    BaseType = type table [ID = number, Name = text],\n"
        '    AddKey = Type.AddTableKey(BaseType, {"ID"}, true),\n'
        "    DetailsOfKeys = Type.TableKeys(AddKey)\n"
        "in\n"
        "    DetailsOfKeys"
    )
    assert result == [{"Columns": ["ID"], "Primary": True}]


def test_replace_table_keys_matches_the_doc_examples():
    replaced = evaluate(
        "let\n"
        "    BaseType = type table [ID = number, FirstName = text, LastName = text],\n"
        "    KeysAdded = Type.ReplaceTableKeys(\n"
        "        BaseType,\n"
        "        {\n"
        '            [Columns = {"ID"}, Primary = true],\n'
        '            [Columns = {"FirstName", "LastName"}, Primary = false]\n'
        "        }\n"
        "    ),\n"
        "    DetailsOfKeys = Type.TableKeys(KeysAdded)\n"
        "in\n"
        "    DetailsOfKeys"
    )
    assert replaced == [
        {"Columns": ["ID"], "Primary": True},
        {"Columns": ["FirstName", "LastName"], "Primary": False},
    ]
    cleared = evaluate(
        "let\n"
        "    TypeWithKey = Type.AddTableKey(type table [ID = number, Name = text],"
        ' {"ID"}, true),\n'
        "    KeyRemoved = Type.ReplaceTableKeys(TypeWithKey, {}),\n"
        "    DetailsOfKeys = Type.TableKeys(KeyRemoved)\n"
        "in\n"
        "    DetailsOfKeys"
    )
    assert cleared == []


def test_add_table_key_rejects_an_unknown_column():
    with pytest.raises(EvalError, match="not found"):
        evaluate('Type.AddTableKey(type table [A = text], {"Z"}, true)')


def test_replace_table_keys_rejects_two_primary_keys():
    with pytest.raises(EvalError, match="at most one primary key"):
        evaluate(
            "Type.ReplaceTableKeys(type table [A = text, B = number], "
            '{[Columns = {"A"}, Primary = true], [Columns = {"B"}, Primary = true]})'
        )


def test_add_table_key_rejects_a_second_primary_key():
    # Same invariant as Type.ReplaceTableKeys, applied incrementally: adding
    # a second primary key one at a time must be caught exactly as adding
    # both at once would be - a table type cannot end up with two primary
    # keys just because they arrived through two calls instead of one list.
    with pytest.raises(EvalError, match="at most one primary key"):
        evaluate(
            "let\n"
            "    T = type table [A = text, B = number],\n"
            '    K1 = Type.AddTableKey(T, {"A"}, true)\n'
            "in\n"
            '    Type.AddTableKey(K1, {"B"}, true)'
        )


# --------------------------------------------------------------------------
# Type.TablePartitionKey / Type.ReplaceTablePartitionKey
# --------------------------------------------------------------------------


def test_table_partition_key_is_null_until_set():
    # "if it has one" - a table type nothing has configured must answer
    # null, not an empty list (those are different states this evaluator
    # can and does distinguish - see Type.ReplaceTablePartitionKey(..., {})
    # below).
    assert evaluate("Type.TablePartitionKey(type table [A = text])") is None


def test_replace_table_partition_key_round_trips():
    result = evaluate(
        "Type.TablePartitionKey("
        'Type.ReplaceTablePartitionKey(type table [A = text], {"A"}))'
    )
    assert result == ["A"]


def test_replace_table_partition_key_with_null_clears_it():
    result = evaluate(
        "Type.TablePartitionKey(Type.ReplaceTablePartitionKey("
        'Type.ReplaceTablePartitionKey(type table [A = text], {"A"}), null))'
    )
    assert result is None


# --------------------------------------------------------------------------
# Type.TableColumn / Type.TableRow / Type.TableSchema
# --------------------------------------------------------------------------


def test_table_column_answers_now_that_the_parser_captures_field_types():
    """This asserted a refusal, and the refusal was the right call at the time.

    `type table [A = text]` really does declare A as text; the evaluator's
    type-expression reader just discarded everything after the field NAME,
    so answering would have meant guessing `type any`. That reader now
    resolves each field's declared type, so the honest answer exists and the
    refusal became the wrong behaviour.

    The refusal is still reachable and still correct - see
    test_table_column_still_refuses_when_the_field_types_are_unknown.
    """
    assert evaluate('Type.TableColumn(type table [A = text, B = number], "A")') == (
        evaluate("type text")
    )


def test_table_column_still_refuses_when_the_field_types_are_unknown():
    """The `field_types is None` path did not go away, it got narrower.

    Power BI writes field types this evaluator cannot model (a `meta`-wrapped
    type bound to a variable), and those degrade to "not captured" rather
    than failing the query. Asking for a column type in that state must
    still refuse rather than invent one.
    """
    source = """
    let
        _t = ((type text) meta [Serialized.Text = true]),
        columns = type table [A = _t]
    in
        Type.TableColumn(columns, "A")
    """
    with pytest.raises(UnsupportedError, match="field types"):
        evaluate(source)


def test_table_column_still_reports_an_unknown_column_as_a_real_error():
    # The column-existence check must run BEFORE the field-type refusal:
    # asking about a column that was never declared is a query bug, not a
    # missing pqtools feature, and the two must not be confused.
    with pytest.raises(EvalError, match="column not found"):
        evaluate('Type.TableColumn(type table [A = text], "Z")')


def test_table_row_answers_for_the_same_reason_table_column_does():
    """Was a refusal, for the same now-closed parser gap."""
    row = evaluate("Type.TableRow(type table [A = text, B = number])")
    assert evaluate("Type.RecordFields(r)", bindings={"r": row}) == {
        "A": {"Type": evaluate("type text"), "Optional": False},
        "B": {"Type": evaluate("type number"), "Optional": False},
    }


def test_table_row_succeeds_when_built_from_a_record_type_with_known_field_types():
    # Type.TableRow's row type is a record type built from the table's
    # column types - fully answerable once those types ARE known, which
    # only ever happens today for a table type this module built itself.
    # There is no M-level way to hand Type.TableRow such a table type (M
    # itself has no Type.ForTable), so this reaches through Type.ForRecord
    # and asserts the shape Type.TableRow's OWN doc example expects one
    # column to have: `[Column1 = [Type = type any, Optional = false]]`.
    one_column_record = (
        "Type.ForRecord([Column1 = [Type = type any, Optional = false]], false)"
    )
    row_type = evaluate(one_column_record)
    assert evaluate(f"Type.RecordFields({one_column_record})") == {
        "Column1": {"Type": evaluate("type any"), "Optional": False}
    }
    assert row_type.kind == "record"


def test_table_schema_fills_the_type_columns_it_can_now_derive():
    """TypeName and Kind were null here because the parser dropped field types.

    It captures them now, so the two columns that are derivable from the
    declared type carry real values. IsNullable stays null on purpose and
    that is not the same gap: this codebase's own Table.Schema defines
    IsNullable as a DATA question ("does any row hold null"), and a bare
    type has no rows to answer it from.
    """
    result = evaluate("Type.TableSchema(type table [A = text, B = number])")
    assert result == [
        {
            "Name": "A",
            "Position": 0,
            "TypeName": "text",
            "Kind": "text",
            "IsNullable": None,
            "NumericPrecision": None,
            "NumericScale": None,
            "NativeTypeName": None,
            "Description": None,
        },
        {
            "Name": "B",
            "Position": 1,
            "TypeName": "number",
            "Kind": "number",
            "IsNullable": None,
            "NumericPrecision": None,
            "NumericScale": None,
            "NativeTypeName": None,
            "Description": None,
        },
    ]


def test_table_schema_on_an_empty_table_type_is_an_empty_table():
    assert evaluate("Type.TableSchema(type table [])") == []


# --------------------------------------------------------------------------
# Type.Facets / Type.ReplaceFacets
# --------------------------------------------------------------------------


def test_facets_defaults_to_an_empty_record():
    assert evaluate("Type.Facets(type number)") == {}


def test_replace_facets_round_trips():
    result = evaluate(
        'Type.Facets(Type.ReplaceFacets(type number, [Precision = "Double"]))'
    )
    assert result == {"Precision": "Double"}


def test_replace_facets_replaces_rather_than_merges():
    # "Replaces the facets of type with the facets contained in the record
    # facets" - a second call must wipe the first set, not add to it.
    result = evaluate(
        "Type.Facets(Type.ReplaceFacets("
        "Type.ReplaceFacets(type number, [A = 1]), [B = 2]))"
    )
    assert result == {"B": 2}


def test_replace_facets_does_not_mutate_the_shared_primitive_singleton():
    # `type number` is one shared, cached _MType instance across every
    # evaluation - Type.ReplaceFacets must build a NEW value, never mutate
    # that instance, or every later `type number` anywhere in the process
    # would silently pick up facets nobody there asked for.
    evaluate('Type.ReplaceFacets(type number, [Precision = "Double"])')
    assert evaluate("Type.Facets(type number)") == {}


# --------------------------------------------------------------------------
# Type.Union
# --------------------------------------------------------------------------


def test_union_matches_any_member_via_value_is():
    assert evaluate("Value.Is(1, Type.Union({type number, type text}))") is True
    assert evaluate('Value.Is("x", Type.Union({type number, type text}))') is True
    assert evaluate("Value.Is(true, Type.Union({type number, type text}))") is False


def test_union_on_the_right_of_type_is_matches_any_member():
    assert evaluate("Type.Is(Int64.Type, Type.Union({type number, type text}))") is True
    assert (
        evaluate("Type.Is(type logical, Type.Union({type number, type text}))") is False
    )


def test_union_on_the_left_of_type_is_requires_every_member_to_conform():
    # Union(Int64.Type, type number) conforms to `type number` because BOTH
    # branches do; Union(Int64.Type, type text) does not, because text
    # never satisfies `type number`.
    assert (
        evaluate("Type.Is(Type.Union({Int64.Type, type number}), type number)") is True
    )
    assert (
        evaluate("Type.Is(Type.Union({Int64.Type, type text}), type number)") is False
    )


def test_union_rejects_a_non_type_member():
    with pytest.raises(EvalError, match="Type.Union"):
        evaluate("Type.Union({type number, 1})")


# --------------------------------------------------------------------------
# Type.ListItem - always refused, never a parser gap that could close
# --------------------------------------------------------------------------


def test_list_item_is_refused_unconditionally():
    with pytest.raises(UnsupportedError, match="Type.ListItem"):
        evaluate("Type.ListItem(type table [A = text])")
    with pytest.raises(UnsupportedError, match="Type.ListItem"):
        evaluate("Type.ListItem(type number)")


def test_list_item_still_validates_its_argument_is_a_type_value():
    with pytest.raises(EvalError, match="Type.ListItem"):
        evaluate('Type.ListItem("not a type")')


# --------------------------------------------------------------------------
# Value.Alternates / Value.Expression - always refused
# --------------------------------------------------------------------------


def test_value_alternates_is_refused():
    with pytest.raises(UnsupportedError, match="Value.Alternates"):
        evaluate("Value.Alternates({1, 2})")


def test_value_expression_is_refused_for_every_value_shape():
    with pytest.raises(UnsupportedError, match="Value.Expression"):
        evaluate("Value.Expression(1)")
    with pytest.raises(UnsupportedError, match="Value.Expression"):
        evaluate('Value.Expression("text")')
    with pytest.raises(UnsupportedError, match="Value.Expression"):
        evaluate("Value.Expression([A = 1])")


# --------------------------------------------------------------------------
# Value.ReplaceType - always refused (mirrors Value.ReplaceMetadata)
# --------------------------------------------------------------------------


def test_value_replace_type_is_refused():
    with pytest.raises(UnsupportedError, match="Value.ReplaceType"):
        evaluate("Value.ReplaceType([Column1 = 123], type table [Column1 = number])")


def test_value_replace_type_still_validates_its_type_argument():
    with pytest.raises(EvalError, match="Value.ReplaceType"):
        evaluate('Value.ReplaceType(1, "not a type")')


# --------------------------------------------------------------------------
# Value.VersionIdentity / Value.Versions - provably null/empty, always
# --------------------------------------------------------------------------


def test_version_identity_is_always_null():
    assert evaluate("Value.VersionIdentity(1)") is None
    assert evaluate('Value.VersionIdentity("text")') is None
    assert evaluate("Value.VersionIdentity(null)") is None


def test_versions_is_always_an_empty_table():
    assert evaluate("Value.Versions(1)") == []
    assert evaluate('Value.Versions("text")') == []
