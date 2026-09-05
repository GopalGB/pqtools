"""What `type ...` written in a query actually evaluates to.

The type-expression evaluator read only the outermost layer of a type, and
two of the three consequences were silent rather than loud - which is why
none of the 2,600 tests before these caught them.

1. `type table [A = table [C = text]]` produced a table with TWO columns,
   `A` and a phantom `C`, because the field search walked every descendant
   instead of the direct children. `Table.ColumnNames` on it returned a
   column the query never declared. No error, anywhere.
2. `type table rowType` - the form where the row shape lives in a variable -
   produced a table type with ZERO columns, because there were no inline
   field specifications to find. That is how Type.ForRecord's own
   documented example failed.
3. Field TYPES and optional flags were discarded entirely, which is what
   forced Type.TableColumn and Type.TableRow to refuse every real input.
"""

from __future__ import annotations

import pytest

from pqtools import UnsupportedError, evaluate


def test_a_nested_field_type_does_not_leak_a_phantom_column() -> None:
    """The silent one. This returned ["A", "C"]."""
    assert evaluate(
        "Table.ColumnNames(#table(type table [A = table [C = text]], {{1}}))"
    ) == ["A"]


def test_a_table_type_named_by_a_variable_carries_its_fields() -> None:
    """Type.ForRecord's own documented shape: `#table(type table rowType, ...)`.

    It used to find no inline field list and quietly return a zero-column
    table type, which only surfaced further downstream as "columns as a type
    without named fields".
    """
    source = """
    let
        rowType = Type.ForRecord(
            [A = [Type = type text, Optional = false],
             B = [Type = type number, Optional = false]],
            false
        ),
        t = #table(type table rowType, {{"x", 1}})
    in
        Table.ColumnNames(t)
    """
    assert evaluate(source) == ["A", "B"]


def test_field_types_and_optional_flags_survive_the_parse() -> None:
    """Discarding these is what made Type.TableColumn refuse every input."""
    assert evaluate('Type.TableColumn(type table [A = text, B = number], "B")') == (
        evaluate("type number")
    )
    fields = evaluate("Type.RecordFields(type [a = text, optional b = number])")
    assert fields["a"] == {"Type": evaluate("type text"), "Optional": False}
    assert fields["b"] == {"Type": evaluate("type number"), "Optional": True}


def test_a_field_with_no_declared_type_is_any() -> None:
    """`type [a]` is legal M; the grammar allows the type to be omitted."""
    fields = evaluate("Type.RecordFields(type [a])")
    assert fields["a"] == {"Type": evaluate("type any"), "Optional": False}


def test_record_types_and_their_openness() -> None:
    assert evaluate("Type.IsOpenRecord(type [a = text, ...])") is True
    assert evaluate("Type.IsOpenRecord(type [a = text])") is False


def test_nullable_is_a_type_pqtools_can_now_write_down() -> None:
    """Type.IsNullable answered a constant `false` while this was unbuildable.

    That was defensible when `type nullable text` raised at parse time. Once
    it evaluates, the constant becomes a wrong answer rather than a missing
    one - which is why the flag and these two functions had to change
    together.
    """
    assert evaluate("Type.IsNullable(type nullable text)") is True
    assert evaluate("Type.IsNullable(type number)") is False
    assert evaluate("Type.NonNullable(type nullable text)") == evaluate("type text")


def test_nullable_field_types_inside_a_table_type() -> None:
    """Table.FromRecords Example 3 declares `nullable text` columns.

    Before field types were read at all, `nullable` inside a field slot was
    invisible - the name was taken and the rest of the field ignored. Now
    that the field type is actually resolved, it has to resolve.
    """
    schema = evaluate(
        "Type.TableSchema(type table [FirstName = nullable text, Age = number])"
    )
    assert [column["Name"] for column in schema] == ["FirstName", "Age"]
    assert [column["TypeName"] for column in schema] == ["text", "number"]


def test_the_power_bi_enter_data_shape_still_works() -> None:
    """The regression that matters most: every "Enter Data" query uses this."""
    assert evaluate(
        'Table.ColumnNames(#table(type table [A = text, B = number], {{"x", 1}}))'
    ) == ["A", "B"]


def test_an_unmodelled_type_shape_is_refused_by_name() -> None:
    """`type {number}` is real M this evaluator does not model.

    It must say so rather than return something plausible - the whole point
    of the change above is that a type it cannot represent is an error, not
    a quietly wrong shape.
    """
    with pytest.raises(UnsupportedError, match="ListType"):
        evaluate("type {number}")


# --------------------------------------------------------------------------
# What a real Power BI workbook actually writes
# --------------------------------------------------------------------------
# Reading field types at all broke the real-workbook end-to-end test three
# separate ways, in sequence. Every one of them was invisible while field
# types were being discarded, and none of the ~2,900 other tests saw any of
# them, because no fixture author writes the shape Power BI generates:
#
#     let _t = ((type text) meta [Serialized.Text = true])
#     in  type table [ID = _t, #"Sales Person" = _t, #"Sales Amount" = _t]
#
# 1. The field types are IDENTIFIERS, not grammar keywords.
# 2. That identifier resolves to a `meta` expression this evaluator refuses
#    on purpose, so one unmodellable field type was fatal to the query.
# 3. The failed binding stayed marked in-progress, so the SECOND column
#    naming `_t` reported a circular reference in a query with no cycle.


def test_a_field_type_can_be_an_identifier_naming_a_type_value() -> None:
    """`type table [Sales = Int64.Type]` is what every Changed Type step emits.

    `Int64.Type` is a registered value, not a grammar keyword, so it parses
    as an identifier - and reading only the keywords made it unresolvable.
    """
    schema = evaluate("Type.TableSchema(type table [A = Int64.Type, B = text])")
    assert [column["TypeName"] for column in schema] == ["number", "text"]


def test_an_unmodellable_field_type_keeps_the_column_names() -> None:
    """Power BI's own `meta`-wrapped field type must not sink the query.

    `meta` is deliberately refused here (there is no value wrapper for it),
    so this degrades to exactly what was true before field types were read:
    the names are certain, the types are "not captured". The Type.*
    functions already treat that state as a refusal rather than a guess, so
    nothing downstream gets a wrong answer - it gets no answer.
    """
    source = """
    let
        _t = ((type text) meta [Serialized.Text = true]),
        t = #table(type table [ID = _t, Name = _t], {{"1", "Bob"}})
    in
        Table.ColumnNames(t)
    """
    assert evaluate(source) == ["ID", "Name"]


def test_a_binding_that_failed_once_reports_its_real_error_the_next_time() -> None:
    """It used to say "circular reference in let binding" - for no cycle.

    `_force` set the in-progress flag and cleared it only on success, so any
    binding that raised stayed marked forever and the next reference was
    misdiagnosed. Power BI hits this immediately: one `_t` named by every
    column means the second column always got the phantom cycle.
    """
    source = """
    let
        broken = Number.FromText("not a number"),
        first = try broken,
        second = try broken
    in
        [a = first[HasError], b = second[HasError], msg = second[Error][Message]]
    """
    result = evaluate(source)
    assert result["a"] is True
    assert result["b"] is True
    # The second reference must report the SAME real failure, not a cycle.
    assert "circular reference" not in result["msg"]
    assert "not a number" in result["msg"]


# --------------------------------------------------------------------------
# The M specification's full primitive-type set
# --------------------------------------------------------------------------
# The spec's `primitive-type` production lists eighteen names:
#
#   any anynonnull binary date datetime datetimezone duration function list
#   logical none null number record table text time type
#
# Eleven were registered. `type list` - which BinaryFormat.Choice's own
# documented example passes - failed as though it were a misspelling.


def test_every_primitive_type_the_spec_names_evaluates() -> None:
    for name in (
        "any",
        "anynonnull",
        "binary",
        "date",
        "datetime",
        "datetimezone",
        "duration",
        "function",
        "list",
        "logical",
        "none",
        "null",
        "number",
        "record",
        "table",
        "text",
        "time",
        "type",
    ):
        assert evaluate(f"type {name}") is not None, name


def test_value_is_answers_for_the_newly_registered_kinds() -> None:
    """Registering a name without teaching Value.Is about it made it LIE.

    `_matches` fell through to `_classify`, which reports None for a list,
    so `Value.Is({1, 2}, type list)` came back FALSE - a wrong answer where
    there had previously been an honest "type value: type list" refusal.
    Making something evaluate is only half the job; the questions askable
    about it have to keep being answered correctly, or the change is a
    regression wearing a feature's clothes.
    """
    assert evaluate("Value.Is({1, 2}, type list)") is True
    assert evaluate("Value.Is(1, type list)") is False
    assert evaluate("Value.Is([a = 1], type record)") is True
    assert evaluate("Value.Is(Number.From, type function)") is True


def test_anynonnull_is_the_one_type_null_does_not_match() -> None:
    """It has to be decided before the "null matches everything" shortcut."""
    assert evaluate("Value.Is(null, type anynonnull)") is False
    assert evaluate("Value.Is(1, type anynonnull)") is True


def test_list_versus_table_still_refuses_when_it_genuinely_cannot_tell() -> None:
    """A table IS a list of records in this data model, so the question has
    no answer for one - and the refusal here must match the one the `is`
    operator already gives, since they answer the same question.
    """
    rows = "{[a = 1], [a = 2]}"
    with pytest.raises(UnsupportedError, match="indistinguishable"):
        evaluate(f"Value.Is({rows}, type list)")
    with pytest.raises(UnsupportedError, match="indistinguishable"):
        evaluate(f"{rows} is list")


def test_a_function_type_parses_into_its_parameters_and_return() -> None:
    """`type function (...) as T` was refused outright.

    That refusal happened one AST node ABOVE the function under test, so
    Function.From's and Function.ScalarVector's own documented examples
    could not run no matter how correctly those two were implemented -
    a gap that looked like their bug and was not.

    `Type.ForFunction` builds the same value from M-level arguments; this
    parses it from source. They must agree, so the display comes from
    ForFunction's own formatter rather than a second spelling.
    """
    declared = "type function (a as number, optional b as text) as number"
    assert evaluate(f"Type.FunctionParameters({declared})") == {
        "a": evaluate("type number"),
        "b": evaluate("type text"),
    }
    # `optional b` does not count toward the required arity.
    assert evaluate(f"Type.FunctionRequiredParameters({declared})") == 1
    assert evaluate(f"Type.FunctionReturn({declared})") == evaluate("type number")


def test_a_function_type_with_no_parameters() -> None:
    assert evaluate("Type.FunctionRequiredParameters(type function () as text)") == 0
