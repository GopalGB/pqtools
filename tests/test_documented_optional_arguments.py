"""The optional arguments thirteen builtins documented but did not accept.

`test_documented_signatures.py` proves the ARITY now matches the page. This
proves the arguments do the documented thing rather than being accepted and
dropped, which is the failure mode a pure arity gate cannot see.

Found by generalising a single Codex finding (`Oracle.Database` took the
wrong argument shape) into "ask that question of all 638 builtins", which
returned thirteen.
"""

from __future__ import annotations

import datetime

import pytest

from pqtools import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# List.Median - comparisonCriteria, plus three behaviours the About text
# states and the implementation did not have.
# --------------------------------------------------------------------------


def test_list_median_takes_comparison_criteria() -> None:
    assert evaluate("List.Median({[a=2],[a=1],[a=3]}, each [a])") == {"a": 2}


def test_list_median_skips_nulls() -> None:
    # "This function returns null if the list contains no non-null values" -
    # which only makes sense if nulls are skipped rather than fatal.
    assert evaluate("List.Median({1, null, 3})") == 2
    assert evaluate("List.Median({null, null})") is None


def test_list_median_of_an_even_non_numeric_list_takes_the_smaller() -> None:
    # "the function chooses the smaller of the two median items unless the
    # list is comprised entirely of datetimes, durations, numbers or times".
    assert evaluate('List.Median({"a", "b", "c", "d"})') == "b"
    # date is pointedly absent from that list, so it takes the same branch.
    assert evaluate("List.Median({#date(2020,1,1), #date(2020,1,3)})") == datetime.date(
        2020, 1, 1
    )


@pytest.mark.parametrize(
    "source,expected",
    [
        ("List.Median({1, 2, 3, 4})", 2.5),
        (
            "List.Median({#duration(0,1,0,0), #duration(0,3,0,0)})",
            datetime.timedelta(hours=2),
        ),
        (
            "List.Median({#datetime(2020,1,1,0,0,0), #datetime(2020,1,3,0,0,0)})",
            datetime.datetime(2020, 1, 2),
        ),
        ("List.Median({#time(1,0,0), #time(3,0,0)})", datetime.time(2, 0)),
    ],
)
def test_list_median_averages_the_four_types_the_page_names(
    source: str, expected: object
) -> None:
    assert evaluate(source) == expected


def test_the_documented_list_median_example_still_holds() -> None:
    assert evaluate("List.Median({5, 3, 1, 7, 9})") == 5


# --------------------------------------------------------------------------
# Culture arguments - accepted, and refused by name when they would change
# the answer.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ('Text.Lower("AbCd", "en-US")', "abcd"),
        ('Text.Upper("aBcD", "en-US")', "ABCD"),
        ('Number.From("5", "en-US")', 5),
        ('Text.Lower("AbCd", null)', "abcd"),
    ],
)
def test_an_en_us_culture_is_accepted(source: str, expected: object) -> None:
    assert evaluate(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        'Text.Lower("AbCd", "tr-TR")',
        'Text.Upper("aBcD", "tr-TR")',
        'Number.From("1.234", "de-DE")',
    ],
)
def test_a_culture_that_would_change_the_answer_is_refused_by_name(source: str) -> None:
    # Silently applying en-US rules to a tr-TR or de-DE caller is the failure
    # this refusal exists to prevent: "1.234" is 1234 in de-DE.
    with pytest.raises(UnsupportedError, match="culture"):
        evaluate(source)


# --------------------------------------------------------------------------
# Record.AddField / Record.RemoveFields
# --------------------------------------------------------------------------


def test_record_add_field_accepts_delayed_false() -> None:
    assert evaluate('Record.AddField([a=1], "b", 2, false)') == {"a": 1, "b": 2}


def test_record_add_field_refuses_delayed_true_rather_than_faking_it() -> None:
    with pytest.raises(UnsupportedError, match="delayed"):
        evaluate('Record.AddField([a=1], "b", 2, true)')


def test_record_add_field_delayed_must_be_a_logical() -> None:
    with pytest.raises(EvalError, match="logical"):
        evaluate('Record.AddField([a=1], "b", 2, 1)')


def test_record_remove_fields_missing_field_modes() -> None:
    assert evaluate('Record.RemoveFields([a=1,b=2], {"z"}, MissingField.Ignore)') == {
        "a": 1,
        "b": 2,
    }
    assert evaluate('Record.RemoveFields([a=1,b=2], {"z"}, MissingField.UseNull)') == {
        "a": 1,
        "b": 2,
    }
    with pytest.raises(EvalError, match="no such field"):
        evaluate('Record.RemoveFields([a=1,b=2], {"z"})')


# --------------------------------------------------------------------------
# Table.*
# --------------------------------------------------------------------------


def test_table_remove_columns_missing_field_modes() -> None:
    table = '#table({"a","b"},{{1,2}})'
    assert evaluate(f'Table.RemoveColumns({table}, {{"z"}}, MissingField.Ignore)') == [
        {"a": 1, "b": 2}
    ]
    with pytest.raises(EvalError, match="no such column"):
        evaluate(f'Table.RemoveColumns({table}, {{"z"}})')


def test_table_add_index_column_applies_the_declared_column_type() -> None:
    # The UI emits this five-argument form; it was rejected outright.
    rows = evaluate(
        'Table.AddIndexColumn(#table({"a"},{{1},{2}}), "Index", 1, 1, Int64.Type)'
    )
    assert rows == [{"a": 1, "Index": 1}, {"a": 2, "Index": 2}]
    assert all(isinstance(row["Index"], int) for row in rows)


def test_table_add_index_column_rejects_a_non_type_column_type() -> None:
    with pytest.raises(EvalError, match="type value"):
        evaluate('Table.AddIndexColumn(#table({"a"},{{1}}), "Index", 1, 1, "nope")')


def test_table_buffer_accepts_an_options_record() -> None:
    assert evaluate('Table.Buffer(#table({"a"},{{1}}), null)') == [{"a": 1}]
    with pytest.raises(UnsupportedError, match="Nope"):
        evaluate('Table.Buffer(#table({"a"},{{1}}), [Nope = 1])')


def test_table_group_comparer_decides_which_rows_share_a_group() -> None:
    # "if it treats differing keys as equal, a row may be placed in a group
    # whose keys differ from its own" - the page, verbatim. A comparer that
    # calls everything equal therefore collapses the table to one group.
    assert evaluate(
        'Table.Group(#table({"a"},{{1},{2},{3}}), "a", '
        '{{"n", each Table.RowCount(_)}}, GroupKind.Global, (x, y) => 0)'
    ) == [{"a": 1, "n": 3}]
    # and the default still groups by real equality
    assert evaluate(
        'Table.Group(#table({"a"},{{1},{2},{1}}), "a", {{"n", each Table.RowCount(_)}})'
    ) == [{"a": 1, "n": 2}, {"a": 2, "n": 1}]


def test_table_group_comparer_must_return_a_number() -> None:
    with pytest.raises(EvalError, match="comparer must return a number"):
        evaluate(
            'Table.Group(#table({"a"},{{1},{2}}), "a", '
            '{{"n", each Table.RowCount(_)}}, GroupKind.Global, (x, y) => "no")'
        )


def test_table_nested_join_accepts_the_comparers_position_and_refuses_a_value() -> None:
    join = (
        'Table.NestedJoin(#table({"a"},{{1}}), {"a"}, #table({"a"},{{1}}), '
        '{"a"}, "N", JoinKind.Inner, {})'
    )
    assert evaluate(join.replace("{}", "null")) == [{"a": 1, "N": [{"a": 1}]}]
    # documented as "intended for internal use only" with no stated
    # semantics, so a value is refused rather than guessed at
    with pytest.raises(UnsupportedError, match="keyEqualityComparers"):
        evaluate(join.replace("{}", "{1}"))


# --------------------------------------------------------------------------
# Splitter.SplitTextByDelimiter - csvStyle, and the default it implies.
# --------------------------------------------------------------------------


def test_the_documented_splitter_example_is_unchanged() -> None:
    assert evaluate(
        'Splitter.SplitTextByDelimiter(",", QuoteStyle.Csv)("a,""b,c"",d")'
    ) == ["a", "b,c", "d"]


def test_csv_style_decides_which_quotes_are_significant() -> None:
    # "CsvStyle.QuoteAfterDelimiter (default): Quotes in a field are only
    # significant immediately following the delimiter. CsvStyle.QuoteAlways:
    # Quotes in a field are always significant, regardless of where they
    # appear." - Csv.Document, verbatim. The quote in `x"a,b"` is mid-field,
    # so the two styles disagree about whether the comma splits.
    assert evaluate(
        'Splitter.SplitTextByDelimiter(",", QuoteStyle.Csv)("x""a,b""")'
    ) == ['x"a', 'b"']
    assert evaluate(
        'Splitter.SplitTextByDelimiter(",", QuoteStyle.Csv, '
        'CsvStyle.QuoteAlways)("x""a,b""")'
    ) == ['x"a,b"']


def test_csv_style_values_resolve_as_identifiers() -> None:
    # They were consumed by Csv.Document's options record already, but had
    # never been registered, so writing one was "unknown identifier".
    # They resolve to the NUMBERS csvstyle-type documents. This test used to
    # assert the self-naming string, which is what the defect looked like
    # from the inside: registered, resolvable, and the wrong kind of value.
    assert evaluate("CsvStyle.QuoteAfterDelimiter") == 0
    assert evaluate("CsvStyle.QuoteAlways") == 1
    # the literal M number is equally valid and must reach the same splitter
    assert evaluate('Splitter.SplitTextByDelimiter(",", 1, 1)("x""a,b""")') == [
        'x"a,b"'
    ]
