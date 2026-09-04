"""The ``#``-prefixed intrinsics: ``#table``, ``#binary``, ``#shared``.

``#table`` is the gap that motivated this file. It is how every hand-written M
example and every "Enter Data" query spells a literal table, and until 0.8.0 it
failed with ``unknown identifier: #table`` - which reads like a typo rather than
a missing feature. It was found by running the documented add-a-query workflow
end to end on a real Excel workbook, not by reading the registry.
"""

from __future__ import annotations

import pytest

from pqtools import EvalError, UnsupportedError, evaluate


def test_hash_table_with_a_column_name_list() -> None:
    result = evaluate('let S = #table({"Colour", "N"}, {{"Blue", 5}, {"Red", 3}}) in S')
    assert result == [{"Colour": "Blue", "N": 5}, {"Colour": "Red", "N": 3}]


def test_hash_table_with_null_columns_names_them_positionally() -> None:
    # M names the columns Column1..N when the spec is null, which is the same
    # thing Table.FromRows does with the argument omitted.
    assert evaluate("let S = #table(null, {{1, 2}}) in S") == [
        {"Column1": 1, "Column2": 2}
    ]


def test_hash_table_with_a_column_count() -> None:
    assert evaluate("let S = #table(2, {{1, 2}, {3, 4}}) in S") == [
        {"Column1": 1, "Column2": 2},
        {"Column1": 3, "Column2": 4},
    ]


def test_hash_table_with_a_table_type() -> None:
    source = 'let S = #table(type table [A = number, B = text], {{1, "x"}}) in S'
    assert evaluate(source) == [{"A": 1, "B": "x"}]


def test_hash_table_with_no_rows_is_an_empty_table() -> None:
    assert evaluate('let S = #table({"A"}, {}) in S') == []


def test_hash_table_pads_a_short_row_with_null() -> None:
    assert evaluate('let S = #table({"A", "B"}, {{1}}) in S') == [{"A": 1, "B": None}]


def test_hash_table_result_flows_into_the_table_functions() -> None:
    # The point of the literal is to be a table, not a lookalike: it has to be
    # accepted by the rest of the library without conversion.
    source = (
        'let T = #table({"n"}, {{3}, {1}, {2}}), S = Table.Sort(T, {{"n", 0}}) in S'
    )
    assert evaluate(source) == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_hash_binary_from_byte_values() -> None:
    assert evaluate("let S = #binary({0, 1, 255}) in S") == b"\x00\x01\xff"


def test_hash_binary_from_base64_text() -> None:
    # The spelling Power Query itself emits for an "Enter Data" payload.
    assert evaluate('let S = #binary("AAEC") in S') == b"\x00\x01\x02"


def test_hash_binary_rejects_a_value_outside_the_byte_range() -> None:
    with pytest.raises(EvalError, match="not a byte value"):
        evaluate("let S = #binary({256}) in S")


def test_hash_binary_rejects_a_non_list_non_text_argument() -> None:
    with pytest.raises(EvalError, match="byte values or base64 text"):
        evaluate("let S = #binary(1.5) in S")


@pytest.mark.parametrize("name", ["#shared", "#sections"])
def test_document_scoped_intrinsics_say_what_they_need(name: str) -> None:
    # Both need the enclosing section document. "unknown identifier" would
    # send the reader looking for a typo instead of at the real limit, so the
    # message names the limit and points at the command that does have it.
    with pytest.raises(UnsupportedError) as caught:
        evaluate(f"let S = {name} in S")
    message = str(caught.value)
    assert "section document" in message
    assert "pq eval FILE --member" in message
