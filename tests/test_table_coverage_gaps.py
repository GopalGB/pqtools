"""Builtins added by a coverage probe of the functions the
Power Query UI actually writes.

The empty-list case in ``Table.ExpandListColumn`` is the one worth guarding:
getting it backwards drops real rows and reports success, which is the silent
wrongness class this project treats as the worst failure mode.
"""

from __future__ import annotations

import pytest

from pqtools.builtins import BUILTINS
from pqtools.builtins._shared import EvalError


def _call(name: str, *args: object) -> object:
    return BUILTINS[name](list(args), None)


ROWS = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}]


def test_reverse_rows() -> None:
    assert _call("Table.ReverseRows", ROWS) == list(reversed(ROWS))


def test_reverse_rows_does_not_mutate_input() -> None:
    original = [dict(r) for r in ROWS]
    _call("Table.ReverseRows", ROWS)
    assert ROWS == original


def test_repeat() -> None:
    assert _call("Table.Repeat", ROWS[:1], 3) == [{"a": 1, "b": "x"}] * 3


def test_repeat_zero_is_empty() -> None:
    assert _call("Table.Repeat", ROWS, 0) == []


def test_repeat_negative_raises() -> None:
    with pytest.raises(EvalError, match="must not be negative"):
        _call("Table.Repeat", ROWS, -1)


def test_expand_list_column_splits_into_rows() -> None:
    table = [{"Name": ["Bob", "Jim", "Paul"], "Discount": 0.15}]
    assert _call("Table.ExpandListColumn", table, "Name") == [
        {"Name": "Bob", "Discount": 0.15},
        {"Name": "Jim", "Discount": 0.15},
        {"Name": "Paul", "Discount": 0.15},
    ]


def test_expand_list_column_empty_list_keeps_row_with_null() -> None:
    # The regression that matters: an empty list must NOT delete the row.
    table = [{"k": 1, "vals": []}, {"k": 2, "vals": ["a"]}]
    assert _call("Table.ExpandListColumn", table, "vals") == [
        {"k": 1, "vals": None},
        {"k": 2, "vals": "a"},
    ]


def test_expand_list_column_null_keeps_row() -> None:
    assert _call("Table.ExpandListColumn", [{"k": 1, "vals": None}], "vals") == [
        {"k": 1, "vals": None}
    ]


def test_expand_list_column_nested_table_is_a_list_of_records() -> None:
    table = [{"Part": "Tool", "Components": [{"Name": "Widget", "Quantity": 3}]}]
    assert _call("Table.ExpandListColumn", table, "Components") == [
        {"Part": "Tool", "Components": {"Name": "Widget", "Quantity": 3}}
    ]


def test_expand_list_column_missing_column_raises() -> None:
    with pytest.raises(EvalError, match="column not found"):
        _call("Table.ExpandListColumn", ROWS, "nope")


def test_expand_list_column_scalar_raises_rather_than_guessing() -> None:
    with pytest.raises(EvalError, match="expected a list"):
        _call("Table.ExpandListColumn", [{"v": 7}], "v")


def test_select_rows_with_errors_is_empty_not_everything() -> None:
    # Returning the whole table would be the dangerous wrong answer.
    assert _call("Table.SelectRowsWithErrors", ROWS) == []
    assert _call("Table.SelectRowsWithErrors", ROWS, ["a"]) == []


def test_replace_error_values_is_identity() -> None:
    assert _call("Table.ReplaceErrorValues", ROWS, [["a", 0]]) == ROWS


def test_replace_error_values_validates_shape() -> None:
    with pytest.raises(EvalError, match="each replacement must be"):
        _call("Table.ReplaceErrorValues", ROWS, [["a"]])
