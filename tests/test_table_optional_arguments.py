"""Optional arguments the Table.* functions are documented to take.

Same finding as tests/test_list_criteria.py, in the other half of the
language. Every gap here is an argument Microsoft's own reference page
declares in its Syntax block and exercises in a worked example, and every
one raised an arity or "no such column" error in pqtools.

Two of them are worth calling out because they were not merely absent:

- `Table.FromRecords` accepted one argument. Six examples across the
  harvested corpus failed on it, most of them belonging to OTHER functions
  that simply build their input with the two-argument form. A missing
  optional argument on a constructor is not one broken function, it is a
  broken idiom.
- Equation criteria for tables is one concept with three documented shapes.
  `Table.Distinct` implemented only the column-list shape and
  `Table.RemoveMatchingRows` only the function shapes, so each rejected the
  other's documented examples. They share one resolver now.
"""

from __future__ import annotations

import datetime

import pytest

from pqtools import UnsupportedError, evaluate

CUSTOMERS = (
    "Table.FromRecords({"
    '[CustomerID = 1, Name = "Bob", Phone = "123-4567"],'
    '[CustomerID = 2, Name = "Jim", Phone = "987-6543"]'
    "})"
)


# --------------------------------------------------------------------------
# Table.FromRecords - columns and missingField
# --------------------------------------------------------------------------


def test_from_records_columns_select_and_reorder() -> None:
    """A `columns` list decides which columns exist, and in what order."""
    assert evaluate('Table.FromRecords({[a = 1, b = 2, c = 3]}, {"c", "a"})') == [
        {"c": 3, "a": 1}
    ]


def test_from_records_accepts_a_table_type_as_columns() -> None:
    """ "A list of the table's column names, OR the table's type" - verbatim."""
    assert evaluate("Table.FromRecords({[a = 1, b = 2]}, type table [b = number])") == [
        {"b": 2}
    ]


def test_from_records_use_null_fills_absent_fields() -> None:
    """Example 3 verbatim: ragged records, squared off with nulls.

    Note CustomerID is dropped - it is not in the declared type - so this
    pins the selection and the null-filling in one query, exactly as the
    page does.
    """
    records = (
        "{"
        '[CustomerID = 1, FirstName = "Bob", MiddleInitial = "C", LastName = "Smith"],'
        '[CustomerID = 2, FirstName = "Sarah", LastName = "Jones"],'
        '[CustomerID = 3, FirstName = "Harry", MiddleInitial = "H"]'
        "}"
    )
    declared = (
        "type table [FirstName = nullable text, MiddleInitial = nullable text, "
        "LastName = nullable text]"
    )
    assert evaluate(
        f"Table.FromRecords({records}, {declared}, MissingField.UseNull)"
    ) == [
        {"FirstName": "Bob", "MiddleInitial": "C", "LastName": "Smith"},
        {"FirstName": "Sarah", "MiddleInitial": None, "LastName": "Jones"},
        {"FirstName": "Harry", "MiddleInitial": "H", "LastName": None},
    ]


def test_from_records_errors_on_a_missing_field_by_default() -> None:
    with pytest.raises(Exception, match="missing field"):
        evaluate('Table.FromRecords({[a = 1]}, {"a", "b"})')


def test_from_records_rejects_missing_field_ignore() -> None:
    """ "Using MissingField.Ignore in this parameter produces an error."

    Quoted from the page, and structural: every row of a table shares one
    column set, so "ignore" has no meaning here. Accepting it silently would
    produce ragged rows that are not a table at all.
    """
    with pytest.raises(Exception, match="MissingField.Ignore is not valid"):
        evaluate('Table.FromRecords({[a = 1]}, {"a", "b"}, MissingField.Ignore)')


# --------------------------------------------------------------------------
# missingField on the column operations
# --------------------------------------------------------------------------


def test_select_columns_use_null_adds_the_missing_column() -> None:
    assert evaluate(
        f'Table.SelectColumns({CUSTOMERS}, {{"Name", "Email"}}, MissingField.UseNull)'
    ) == [{"Name": "Bob", "Email": None}, {"Name": "Jim", "Email": None}]


def test_select_columns_ignore_drops_the_missing_column() -> None:
    assert evaluate(
        f'Table.SelectColumns({CUSTOMERS}, {{"Name", "Email"}}, MissingField.Ignore)'
    ) == [{"Name": "Bob"}, {"Name": "Jim"}]


def test_select_columns_still_errors_by_default() -> None:
    """The default must not drift: a typo'd column name is a real bug."""
    with pytest.raises(Exception, match="no such column: Email"):
        evaluate(f'Table.SelectColumns({CUSTOMERS}, {{"Name", "Email"}})')


def test_rename_columns_ignore_is_the_defensive_form_real_queries_use() -> None:
    assert evaluate(
        'Table.RenameColumns(Table.FromRecords({[a = 1]}), {{"z", "y"}}, '
        "MissingField.Ignore)"
    ) == [{"a": 1}]


def test_rename_columns_use_null_creates_the_renamed_column_empty() -> None:
    """Derived, not copied: the page names MissingField.UseNull as accepted
    but shows no worked example of it on a rename. This reads the enum's own
    definition - "any missing fields are included as null values" - so the
    absent column arrives under its NEW name, empty. If that ever proves
    wrong, this is the assumption to revisit.
    """
    assert evaluate(
        'Table.RenameColumns(Table.FromRecords({[a = 1]}), {{"z", "y"}}, '
        "MissingField.UseNull)"
    ) == [{"a": 1, "y": None}]


def test_reorder_columns_takes_missing_field() -> None:
    """It used to refuse the argument outright with UnsupportedError."""
    assert evaluate(
        'Table.ReorderColumns(Table.FromRecords({[a = 1, b = 2]}), {"z", "b"}, '
        "MissingField.Ignore)"
    ) == [{"b": 2, "a": 1}]
    assert evaluate(
        'Table.ReorderColumns(Table.FromRecords({[a = 1, b = 2]}), {"z", "b"}, '
        "MissingField.UseNull)"
    ) == [{"z": None, "b": 2, "a": 1}]


# --------------------------------------------------------------------------
# Table.TransformColumns - the other half of its signature
# --------------------------------------------------------------------------


def test_transform_columns_takes_a_new_column_type() -> None:
    """ "{ column name, transformation, new column type }" - the three-element
    form the page declares. Several doc examples use it, and all of them hit
    "expected a {column, value} pair" instead.
    """
    assert evaluate(
        'Table.TransformColumns(Table.FromRecords({[A = "1", B = 2]}), '
        '{{"A", Number.FromText, Int64.Type}, {"B", Text.From}})'
    ) == [{"A": 1, "B": "2"}]


def test_transform_columns_applies_a_default_transformation() -> None:
    """defaultTransformation hits every column NOT listed - so a query that
    normalises one column by name and the rest generically works.
    """
    assert evaluate(
        "Table.TransformColumns(Table.FromRecords({[a = 1, b = 2, c = 3]}), "
        '{{"a", each _ * 100}}, each _ + 1)'
    ) == [{"a": 100, "b": 3, "c": 4}]


def test_transform_columns_missing_field() -> None:
    assert evaluate(
        "Table.TransformColumns(Table.FromRecords({[a = 1]}), "
        '{{"z", each 5}}, null, MissingField.Ignore)'
    ) == [{"a": 1}]
    assert evaluate(
        "Table.TransformColumns(Table.FromRecords({[a = 1]}), "
        '{{"z", each _ = null}}, null, MissingField.UseNull)'
    ) == [{"a": 1, "z": True}]


def test_transform_columns_still_errors_by_default() -> None:
    with pytest.raises(Exception, match="no such column: z"):
        evaluate(
            'Table.TransformColumns(Table.FromRecords({[a = 1]}), {{"z", each 5}})'
        )


# --------------------------------------------------------------------------
# Table.FirstN / Table.LastN - countOrCondition
# --------------------------------------------------------------------------


def test_first_n_takes_a_condition_and_stops_at_the_first_failure() -> None:
    """Example 2. The parameter is `countOrCondition`, and the condition half
    was missing, so it raised "expected a number, got _Lambda".

    The stop-at-first-failure part is the whole difference from
    Table.SelectRows, and it is what the second assertion pins: the third row
    matches the predicate but is never reached.
    """
    table = "Table.FromRecords({[a = 1, b = 2], [a = 3, b = 4], [a = -5, b = -6]})"
    assert evaluate(f"Table.FirstN({table}, each [a] > 0)") == [
        {"a": 1, "b": 2},
        {"a": 3, "b": 4},
    ]
    stops = "Table.FromRecords({[a = 1], [a = -1], [a = 2]})"
    assert evaluate(f"Table.FirstN({stops}, each [a] > 0)") == [{"a": 1}]


def test_last_n_takes_a_condition_and_scans_backwards() -> None:
    """Example 2, and the mirror image: it scans from the END while the
    condition holds, then returns what it kept in ascending position.
    """
    table = "Table.FromRecords({[a = -1, b = -2], [a = 3, b = 4], [a = 5, b = 6]})"
    assert evaluate(f"Table.LastN({table}, each _[a] > 0)") == [
        {"a": 3, "b": 4},
        {"a": 5, "b": 6},
    ]


def test_first_n_and_last_n_still_take_a_count() -> None:
    table = "Table.FromRecords({[a = 1], [a = 2], [a = 3]})"
    assert evaluate(f"Table.FirstN({table}, 2)") == [{"a": 1}, {"a": 2}]
    assert evaluate(f"Table.LastN({table}, 2)") == [{"a": 2}, {"a": 3}]


# --------------------------------------------------------------------------
# Equation criteria for tables - one concept, three shapes
# --------------------------------------------------------------------------


def test_remove_matching_rows_takes_a_column_name() -> None:
    """Example 1 passes a bare "a" - the singular of "a list of the columns"."""
    table = "Table.FromRecords({[a = 1, b = 2], [a = 3, b = 4], [a = 1, b = 6]})"
    assert evaluate(f'Table.RemoveMatchingRows({table}, {{[a = 1]}}, "a")') == [
        {"a": 3, "b": 4}
    ]


def test_remove_matching_rows_takes_a_column_list() -> None:
    """A one-element list used to be read as a {selector, comparer} pair and
    rejected for "not having exactly two items"."""
    table = (
        "#table(type table [Task = text, Date = date], "
        '{{"A", #date(2025, 7, 10)}, {"B", #date(2025, 9, 1)}})'
    )
    assert evaluate(
        f'Table.RemoveMatchingRows({table}, {{[Date = #date(2025, 9, 1)]}}, {{"Date"}})'
    ) == [{"Task": "A", "Date": datetime.date(2025, 7, 10)}]


def test_distinct_takes_a_key_selector() -> None:
    """Table.Distinct understood only the column-list shape."""
    table = 'Table.FromRecords({[a = "A", b = "x"], [a = "A", b = "y"]})'
    assert evaluate(f"Table.Distinct({table}, each [a])") == [{"a": "A", "b": "x"}]


def test_distinct_still_takes_a_column_list() -> None:
    table = "Table.FromRecords({[a = 1, b = 2], [a = 1, b = 3], [a = 2, b = 4]})"
    assert evaluate(f'Table.Distinct({table}, "a")') == [
        {"a": 1, "b": 2},
        {"a": 2, "b": 4},
    ]


# --------------------------------------------------------------------------
# Table.Combine / Table.TransformColumnNames
# --------------------------------------------------------------------------


def test_combine_columns_option_defines_the_result_shape() -> None:
    """ "a row type structure defined by columns, or by a union of the input
    types if columns is not specified" - so it replaces the union rather
    than filtering it. The argument used to be refused outright.
    """
    tables = (
        'Table.FromRecords({[Name = "Bob", Phone = "1"]}), '
        'Table.FromRecords({[Name = "Jim", Fax = "2"]})'
    )
    assert evaluate(f'Table.Combine({{{tables}}}, {{"Name"}})') == [
        {"Name": "Bob"},
        {"Name": "Jim"},
    ]
    assert evaluate(f"Table.Combine({{{tables}}})") == [
        {"Name": "Bob", "Phone": "1", "Fax": None},
        {"Name": "Jim", "Phone": None, "Fax": "2"},
    ]


def test_transform_column_names_options() -> None:
    """Example 2 verbatim, and it pins three behaviours at once.

    MaxLength trims to six; Comparer.OrdinalIgnoreCase makes "cOlumn" and
    "coLumn" collide with "Column"; and the disambiguating counter is
    appended INSIDE the length limit, which is why the answer is "cOlum1"
    and not "cOlumn1".
    """
    table = "Table.FromRecords({[ColumnNum = 1, cOlumnnum = 2, coLumnNUM = 3]})"
    assert evaluate(
        f"Table.TransformColumnNames({table}, Text.Clean, "
        "[MaxLength = 6, Comparer = Comparer.OrdinalIgnoreCase])"
    ) == [{"Column": 1, "cOlum1": 2, "coLum2": 3}]


def test_transform_column_names_rejects_an_unknown_option() -> None:
    with pytest.raises(UnsupportedError, match="option"):
        evaluate(
            "Table.TransformColumnNames(Table.FromRecords({[a = 1]}), "
            "Text.Upper, [Nonsense = 1])"
        )
