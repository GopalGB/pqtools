"""Tests for ``builtins/_type.py`` - the M type system (PRD-0.5.0-builtins.md P0).

Covers: type-value ascription (``type text`` ... and the ``X.Type``
identifier forms), ``Table.TransformColumnTypes``, ``Table.PromoteHeaders``,
``Table.DemoteHeaders``, and ``Value.Type``/``Value.Is``/``Value.Equals``/
``Value.Compare``/``Type.Is``.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Type values themselves
# --------------------------------------------------------------------------


def test_primitive_type_ascription_round_trips_every_kind():
    for keyword in (
        "text",
        "number",
        "date",
        "datetime",
        "datetimezone",
        "time",
        "duration",
        "logical",
        "any",
        "none",
        "binary",
    ):
        # Each one must at least evaluate without error, and be its own
        # stable, equal-to-itself value.
        assert evaluate(f"type {keyword}") == evaluate(f"type {keyword}")


def test_nominal_identifier_types_resolve():
    for name in (
        "Int64.Type",
        "Percentage.Type",
        "Currency.Type",
        "Double.Type",
        "Single.Type",
        "Decimal.Type",
        "Byte.Type",
        "Int8.Type",
        "Int16.Type",
        "Int32.Type",
        "Text.Type",
        "Number.Type",
        "Date.Type",
        "DateTime.Type",
        "Logical.Type",
        "Any.Type",
    ):
        # Must resolve as a bare identifier (never invoked) with no error.
        assert evaluate(name) is not None


def test_alias_identifiers_are_the_same_type_as_their_primitive_spelling():
    assert evaluate("Text.Type") == evaluate("type text")
    assert evaluate("Number.Type") == evaluate("type number")
    assert evaluate("Date.Type") == evaluate("type date")
    assert evaluate("DateTime.Type") == evaluate("type datetime")
    assert evaluate("Logical.Type") == evaluate("type logical")
    assert evaluate("Any.Type") == evaluate("type any")


def test_nominal_number_subtype_is_not_equal_to_plain_number_type():
    # Int64.Type converts differently (rounds + range-checks) from `type
    # number`, so they must be distinct type values even though both
    # satisfy Type.Is(X, type number) - see the next test.
    assert evaluate("Int64.Type") != evaluate("type number")


def test_compound_type_shapes_are_unsupported_not_guessed():
    with pytest.raises(UnsupportedError, match="type value"):
        evaluate("type {number}")
    with pytest.raises(UnsupportedError, match="type value"):
        evaluate("type [a = text]")


# --------------------------------------------------------------------------
# Table.TransformColumnTypes
# --------------------------------------------------------------------------


def test_transform_column_types_basic_conversions():
    table = [{"a": "1001", "b": "250.00", "c": "2024-01-15", "d": "hello"}]
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"a", Int64.Type}, {"b", type number}, '
        '{"c", type date}, {"d", type text}})',
        bindings={"t": table},
    )
    assert result == [{"a": 1001, "b": 250.0, "c": dt.date(2024, 1, 15), "d": "hello"}]
    assert isinstance(result[0]["a"], int)
    assert isinstance(result[0]["b"], float)


def test_transform_column_types_null_stays_null_for_every_target_type():
    table = [{"a": None, "b": None, "c": None, "d": None, "e": None}]
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"a", type text}, {"b", type number}, '
        '{"c", type date}, {"d", type logical}, {"e", type duration}})',
        bindings={"t": table},
    )
    assert result == [{"a": None, "b": None, "c": None, "d": None, "e": None}]


def test_transform_column_types_non_numeric_text_to_number_is_an_error_not_null():
    with pytest.raises(EvalError, match="not a number"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"a", type number}})',
            bindings={"t": [{"a": "not a number"}]},
        )


def test_transform_column_types_missing_column_is_eval_error():
    with pytest.raises(EvalError, match="column not found: missing"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"missing", type text}})',
            bindings={"t": [{"a": 1}]},
        )


def test_transform_column_types_malformed_pair_is_eval_error():
    with pytest.raises(EvalError, match="ColumnName"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"a"}})',
            bindings={"t": [{"a": 1}]},
        )


def test_transform_column_types_culture_argument_is_unsupported():
    with pytest.raises(UnsupportedError, match="culture-aware"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"a", type number}}, "en-US")',
            bindings={"t": [{"a": "5"}]},
        )
    # `null` culture means "no culture" and must NOT raise.
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"a", type number}}, null)',
        bindings={"t": [{"a": "5"}]},
    )
    assert result == [{"a": 5}]


def test_transform_column_types_int64_rounds_with_bankers_rounding_not_truncation():
    # Real Power Query's Int64.From uses RoundingMode.ToEven by default
    # (verified against bengribaudo.com's Power Query M Primer and
    # powerquery.how's Table.TransformColumnTypes reference) - Python's
    # round() on a float already implements banker's rounding, so this
    # pins the exact edge cases where truncation-toward-zero would differ:
    # 2.5 -> 2 (not 3), 3.5 -> 4 (not 3), -2.5 -> -2 (not -2 via truncation
    # too, so also check a case truncation gets right but rounds elsewhere).
    table = [{"x": 2.5}, {"x": 3.5}, {"x": 2.4}, {"x": 2.6}]
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"x", Int64.Type}})', bindings={"t": table}
    )
    assert [row["x"] for row in result] == [2, 4, 2, 3]


def test_transform_column_types_int64_vs_type_number_int_vs_float():
    # "Int64.Type truncates [to an int] vs type number keeping the float"
    # - the observable difference is int vs float, not the rounding
    # algorithm (see the banker's-rounding test above for that).
    table = [{"x": 3.7}]
    as_int64 = evaluate(
        'Table.TransformColumnTypes(t, {{"x", Int64.Type}})', bindings={"t": table}
    )
    as_number = evaluate(
        'Table.TransformColumnTypes(t, {{"x", type number}})', bindings={"t": table}
    )
    assert as_int64 == [{"x": 4}]
    assert isinstance(as_int64[0]["x"], int)
    assert as_number == [{"x": 3.7}]
    assert isinstance(as_number[0]["x"], float)


def test_transform_column_types_whole_number_out_of_range_is_eval_error():
    with pytest.raises(EvalError, match="out of range for Byte.Type"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"x", Byte.Type}})',
            bindings={"t": [{"x": 300}]},
        )
    with pytest.raises(EvalError, match="out of range for Int8.Type"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"x", Int8.Type}})',
            bindings={"t": [{"x": -200}]},
        )


def test_transform_column_types_currency_rounds_to_four_decimal_places():
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"x", Currency.Type}})',
        bindings={"t": [{"x": 1.123456}]},
    )
    assert result == [{"x": 1.1235}]


def test_transform_column_types_percentage_is_a_pure_display_facet():
    # Percentage.Type does not multiply/divide the value - it is the same
    # number, just a formatting claim (bengribaudo.com's type-system primer).
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"x", Percentage.Type}})',
        bindings={"t": [{"x": "45"}]},
    )
    assert result == [{"x": 45}]


def test_transform_column_types_datetime_datetimezone_time_duration():
    table = [
        {
            "dt": "2024-03-01T10:30:00",
            "dtz": "2024-03-01T10:30:00+05:30",
            "t": "10:30:00",
            "dur": "1.02:03:04",
        }
    ]
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"dt", type datetime}, '
        '{"dtz", type datetimezone}, {"t", type time}, '
        '{"dur", type duration}})',
        bindings={"t": table},
    )
    row = result[0]
    assert row["dt"] == dt.datetime(2024, 3, 1, 10, 30, 0)
    assert row["dtz"] == dt.datetime(
        2024, 3, 1, 10, 30, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))
    )
    assert row["t"] == dt.time(10, 30, 0)
    assert row["dur"] == dt.timedelta(days=1, hours=2, minutes=3, seconds=4)


def test_transform_column_types_datetimezone_without_offset_is_eval_error():
    with pytest.raises(EvalError, match="no timezone offset"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"x", type datetimezone}})',
            bindings={"t": [{"x": "2024-03-01T10:30:00"}]},
        )


def test_transform_column_types_logical_from_text_and_number():
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"a", type logical}, {"b", type logical}, '
        '{"c", type logical}})',
        bindings={"t": [{"a": "TRUE", "b": "false", "c": 0}]},
    )
    assert result == [{"a": True, "b": False, "c": False}]


def test_transform_column_types_any_is_identity():
    table = [{"x": [1, 2, 3]}]
    result = evaluate(
        'Table.TransformColumnTypes(t, {{"x", type any}})', bindings={"t": table}
    )
    assert result == table


def test_transform_column_types_none_and_binary_targets_are_unsupported():
    with pytest.raises(UnsupportedError, match="target type"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"x", type none}})',
            bindings={"t": [{"x": 1}]},
        )
    with pytest.raises(UnsupportedError, match="target type"):
        evaluate(
            'Table.TransformColumnTypes(t, {{"x", type binary}})',
            bindings={"t": [{"x": 1}]},
        )


def test_transform_column_types_empty_table_returns_empty():
    assert (
        evaluate(
            'Table.TransformColumnTypes(t, {{"a", type text}})', bindings={"t": []}
        )
        == []
    )


# --------------------------------------------------------------------------
# Table.PromoteHeaders / Table.DemoteHeaders
# --------------------------------------------------------------------------


def test_promote_headers_basic():
    table = [
        {"Column1": "OrderID", "Column2": "Customer"},
        {"Column1": "1001", "Column2": "Alice"},
    ]
    result = evaluate("Table.PromoteHeaders(t)", bindings={"t": table})
    assert result == [{"OrderID": "1001", "Customer": "Alice"}]


def test_promote_headers_promote_all_scalars_option():
    table = [
        {"Column1": True, "Column2": dt.date(2024, 1, 1)},
        {"Column1": "x", "Column2": "y"},
    ]
    default = evaluate("Table.PromoteHeaders(t)", bindings={"t": table})
    assert list(default[0].keys()) == ["Column1", "Column2"]

    promoted = evaluate(
        "Table.PromoteHeaders(t, [PromoteAllScalars=true])", bindings={"t": table}
    )
    assert list(promoted[0].keys()) == ["TRUE", "2024-01-01"]


def test_promote_headers_dedupes_duplicate_names():
    table = [
        {"Column1": "a", "Column2": "a"},
        {"Column1": "1", "Column2": "2"},
    ]
    result = evaluate("Table.PromoteHeaders(t)", bindings={"t": table})
    assert list(result[0].keys()) == ["a", "a.1"]


def test_promote_headers_unknown_option_is_unsupported():
    with pytest.raises(UnsupportedError, match="option"):
        evaluate(
            "Table.PromoteHeaders(t, [NotARealOption=true])",
            bindings={"t": [{"Column1": "x"}]},
        )


def test_promote_headers_culture_option_is_unsupported():
    with pytest.raises(UnsupportedError, match="Culture"):
        evaluate(
            'Table.PromoteHeaders(t, [Culture="en-US"])',
            bindings={"t": [{"Column1": "x"}]},
        )


def test_promote_headers_empty_table_returns_empty():
    assert evaluate("Table.PromoteHeaders(t)", bindings={"t": []}) == []


def test_demote_headers_round_trips_with_promote():
    table = [
        {"Column1": "a", "Column2": "b"},
        {"Column1": "1", "Column2": "2"},
    ]
    promoted = evaluate("Table.PromoteHeaders(t)", bindings={"t": table})
    demoted = evaluate("Table.DemoteHeaders(u)", bindings={"u": promoted})
    assert demoted == [
        {"Column1": "a", "Column2": "b"},
        {"Column1": "1", "Column2": "2"},
    ]


def test_demote_headers_empty_table_returns_empty():
    assert evaluate("Table.DemoteHeaders(t)", bindings={"t": []}) == []


# --------------------------------------------------------------------------
# Value.Type / Value.Is / Value.Equals / Value.Compare / Type.Is
# --------------------------------------------------------------------------


def test_value_type_primitives():
    assert evaluate("Value.Type(null)") == evaluate("type none")
    assert evaluate("Value.Type(1)") == evaluate("type number")
    assert evaluate('Value.Type("x")') == evaluate("type text")
    assert evaluate("Value.Type(true)") == evaluate("type logical")


def test_value_type_on_date_built_via_transform_column_types():
    row = evaluate(
        'Table.TransformColumnTypes(t, {{"x", type date}})',
        bindings={"t": [{"x": "2024-01-01"}]},
    )[0]
    assert evaluate("Value.Type(x)", bindings={"x": row["x"]}) == evaluate("type date")


def test_value_type_on_list_or_record_is_unsupported():
    with pytest.raises(UnsupportedError, match="Value.Type"):
        evaluate("Value.Type({1, 2})")
    with pytest.raises(UnsupportedError, match="Value.Type"):
        evaluate("Value.Type([a = 1])")


def test_value_is_matches_and_null_matches_everything():
    assert evaluate("Value.Is(1, type number)") is True
    assert evaluate("Value.Is(1, type text)") is False
    assert evaluate("Value.Is(null, type text)") is True
    assert evaluate("Value.Is(1, Int64.Type)") is True
    assert evaluate('Value.Is("x", Int64.Type)') is False


def test_value_is_against_type_none_is_unsupported():
    with pytest.raises(UnsupportedError, match="type none"):
        evaluate("Value.Is(null, type none)")


def test_value_equals_reuses_m_equality():
    assert evaluate("Value.Equals(1, 1)") is True
    assert evaluate('Value.Equals(1, "1")') is False


def test_value_equals_with_precision_argument_is_unsupported():
    with pytest.raises(UnsupportedError, match="precision"):
        evaluate("Value.Equals(1, 1, 0.001)")


def test_value_compare_numbers_and_text():
    assert evaluate("Value.Compare(1, 2)") == -1
    assert evaluate("Value.Compare(2, 1)") == 1
    assert evaluate("Value.Compare(1, 1)") == 0
    assert evaluate('Value.Compare("a", "b")') == -1


def test_value_compare_cross_type_is_unsupported():
    with pytest.raises(UnsupportedError, match="cross-type"):
        evaluate('Value.Compare(1, "a")')


def test_value_compare_with_explicit_comparer_delegates_to_it():
    comparer = "(a, b) => if a > b then 1 else if a < b then -1 else 0"
    assert evaluate(f"Value.Compare(1, 2, {comparer})") == -1


def test_type_is_number_subtype_compatibility():
    assert evaluate("Type.Is(Int64.Type, type number)") is True
    assert evaluate("Type.Is(type text, type any)") is True
    assert evaluate("Type.Is(type text, type number)") is False
    assert evaluate("Type.Is(type number, type number)") is True
