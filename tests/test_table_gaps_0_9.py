"""Table.* functions that were missing until 0.9.0.

Found by checking the registry against a list of the M functions real queries
actually use, rather than by waiting for someone to hit the gap. 53 of 116
common names were absent; these are the Table ones.
"""

from __future__ import annotations

import pytest

from pqtools import EvalError, evaluate

T = '#table({"a","b"},{{1,"x"},{2,"y"},{3,"x"},{null,"z"}})'


def run(expression: str) -> object:
    return evaluate(f"let T = {T} in {expression}")


def test_buffer_returns_equal_rows() -> None:
    assert run("Table.Buffer(T)") == run("T")


def test_buffer_is_a_copy_not_the_same_rows() -> None:
    # The only thing buffering can still mean in an eager evaluator is "later
    # mutation does not show through". Returning the same objects would make
    # the name a lie, so this checks identity, not equality.
    source = f"let T = {T}, B = Table.Buffer(T) in B"
    buffered = evaluate(source)
    original = evaluate(f"let T = {T} in T")
    assert buffered == original
    assert all(a is not b for a, b in zip(buffered, original, strict=True))


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('Table.HasColumns(T, {"a","b"})', True),
        ('Table.HasColumns(T, {"a","z"})', False),
        ('Table.HasColumns(T, "a")', True),
        ("Table.Contains(T, [a=2])", True),
        ("Table.Contains(T, [a=99])", False),
        ('Table.Contains(T, [a=1, b="x"])', True),
        ('Table.Contains(T, [a=1, b="y"])', False),
    ],
)
def test_membership_predicates(expression: str, expected: bool) -> None:
    assert run(expression) is expected


def test_position_of_returns_minus_one_when_absent() -> None:
    # M's answer for "not found" is -1, not null and not an error. A caller
    # comparing the result to 0 would read null as a match at the first row.
    assert run('Table.PositionOf(T, [b="nope"])') == -1


def test_position_of_finds_the_first_occurrence_by_default() -> None:
    assert run('Table.PositionOf(T, [b="x"])') == 0


def test_position_of_last_occurrence() -> None:
    assert run('Table.PositionOf(T, [b="x"], 1)') == 2


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('Table.MatchesAnyRows(T, each [b] = "y")', True),
        ('Table.MatchesAnyRows(T, each [b] = "q")', False),
        ("Table.MatchesAllRows(T, each [b] <> null)", True),
        ("Table.MatchesAllRows(T, each [a] <> null)", False),
    ],
)
def test_row_predicates(expression: str, expected: bool) -> None:
    assert run(expression) is expected


def test_row_predicate_must_return_a_logical() -> None:
    # Coercing here would hide a query bug behind a plausible answer.
    with pytest.raises(EvalError, match="must return a logical"):
        run("Table.MatchesAnyRows(T, each [a])")


def test_remove_matching_rows_drops_every_match() -> None:
    assert run('Table.RowCount(Table.RemoveMatchingRows(T, {[b="x"]}))') == 2


def test_insert_rows_places_the_row_at_the_offset() -> None:
    result = run('Table.InsertRows(T, 1, {[a=9, b="n"]})')
    assert [row["a"] for row in result] == [1, 9, 2, 3, None]


def test_insert_rows_rejects_an_offset_past_the_end() -> None:
    with pytest.raises(EvalError, match="outside the table"):
        run('Table.InsertRows(T, 99, {[a=1, b="n"]})')


def test_range_with_and_without_a_count() -> None:
    assert [row["a"] for row in run("Table.Range(T, 1, 2)")] == [2, 3]
    assert [row["a"] for row in run("Table.Range(T, 2)")] == [3, None]


def test_split_at_returns_both_halves() -> None:
    halves = run("Table.SplitAt(T, 2)")
    assert [row["a"] for row in halves[0]] == [1, 2]
    assert [row["a"] for row in halves[1]] == [3, None]


def test_alternate_rows_skips_and_takes() -> None:
    # offset 0, skip 1, take 1 keeps rows 1 and 3 (zero-based).
    assert [row["a"] for row in run("Table.AlternateRows(T, 0, 1, 1)")] == [2, None]


def test_alternate_rows_honours_the_offset() -> None:
    assert [row["a"] for row in run("Table.AlternateRows(T, 1, 1, 1)")] == [1, 3]


def test_schema_names_and_orders_the_columns() -> None:
    rows = run("Table.Schema(T)")
    assert [r["Name"] for r in rows] == ["a", "b"]
    assert [r["Position"] for r in rows] == [0, 1]


def test_schema_reports_nullability_from_the_data() -> None:
    rows = run("Table.Schema(T)")
    by_name = {r["Name"]: r for r in rows}
    assert by_name["a"]["IsNullable"] is True
    assert by_name["b"]["IsNullable"] is False


def test_schema_leaves_undeterminable_fields_null() -> None:
    # A precision or native type name this evaluator never saw would be read
    # as fact by anything consuming the schema, so it stays null.
    row = run("Table.Schema(T)")[0]
    assert row["NumericPrecision"] is None
    assert row["NativeTypeName"] is None


def test_profile_counts_nulls_and_distinct_values() -> None:
    rows = run("Table.Profile(T)")
    by_column = {r["Column"]: r for r in rows}
    assert by_column["a"]["NullCount"] == 1
    assert by_column["a"]["Count"] == 4
    assert by_column["b"]["DistinctCount"] == 3


def test_profile_computes_numeric_statistics() -> None:
    row = {r["Column"]: r for r in run("Table.Profile(T)")}["a"]
    assert row["Min"] == 1
    assert row["Max"] == 3
    assert row["Average"] == pytest.approx(2.0)
    assert row["StandardDeviation"] == pytest.approx(1.0)


def test_profile_leaves_standard_deviation_null_for_one_value() -> None:
    # The sample standard deviation of a single value is undefined, not zero.
    single = 'let T = #table({"a"},{{5}}) in Table.Profile(T)'
    assert evaluate(single)[0]["StandardDeviation"] is None
