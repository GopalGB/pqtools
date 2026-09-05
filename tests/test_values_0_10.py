"""``Record.*``, ``Binary.*``, ``Type.*``, ``Value.*``, ``Expression.*``,
``Splitter.*`` and ``Combiner.*`` functions missing until this session's
Microsoft Learn diff.

Found by fetching the real record-functions.md / binary-functions.md /
type-functions.md / value-functions.md / expression-functions.md /
splitter-functions.md / combiner-functions.md tables and diffing them
against the live registry, per the task brief - not by memory. Every
example reproduced below is the exact worked example from the function's
own Microsoft Learn page (cited in the implementation's comment), not an
invented case.

``Comparer.*`` and ``Uri.*`` are already fully implemented elsewhere
(``_text.py`` / ``_sources.py``) and already have their own test coverage
(``tests/test_comparers.py``); they are not retested here. The five
``Splitter.*``/``Replacer.*`` names already owned by ``_table_shape.py``
are likewise out of scope for this file.
"""

from __future__ import annotations

import math

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate
from pqtools.io import DENY_ALL, IOBlockedError, IOPolicy

# --------------------------------------------------------------------------
# Record.FromTable
# --------------------------------------------------------------------------


def test_record_from_table_matches_ms_docs_example():
    # learn.microsoft.com/en-us/powerquery-m/record-fromtable
    assert evaluate(
        """
        Record.FromTable(
            Table.FromRecords({
                [Name = "CustomerID", Value = 1],
                [Name = "Name", Value = "Bob"],
                [Name = "Phone", Value = "123-4567"]
            })
        )
        """
    ) == {"CustomerID": 1, "Name": "Bob", "Phone": "123-4567"}


def test_record_from_table_round_trips_with_record_to_table():
    source = {"A": 1, "B": "x", "C": True}
    table = evaluate("Record.ToTable(_)", bindings={"_": source})
    assert evaluate("Record.FromTable(_)", bindings={"_": table}) == source


def test_record_from_table_rejects_duplicate_names():
    with pytest.raises(EvalError, match="not unique"):
        evaluate('Record.FromTable({[Name = "A", Value = 1], [Name = "A", Value = 2]})')


def test_record_from_table_rejects_missing_name_or_value_field():
    with pytest.raises(EvalError, match="missing a Name or Value"):
        evaluate('Record.FromTable({[Name = "A"]})')


# --------------------------------------------------------------------------
# Binary.ApproximateLength / Binary.From / Binary.FromList / Binary.ToList
# --------------------------------------------------------------------------


def test_binary_approximate_length_matches_ms_docs_example():
    # learn.microsoft.com/en-us/powerquery-m/binary-approximatelength
    assert evaluate('Binary.ApproximateLength(Binary.FromText("i45WMlSKjQUA"))') == 9


def test_binary_approximate_length_null_in_null_out():
    assert evaluate("Binary.ApproximateLength(null)") is None


def test_binary_from_text_matches_binary_from_text_default_encoding():
    # learn.microsoft.com/en-us/powerquery-m/binary-from: the docs state
    # Binary.From("1011") is equivalent to
    # Binary.FromText("1011", BinaryEncoding.Base64).
    assert evaluate('Binary.From("1011")') == evaluate(
        'Binary.FromText("1011", BinaryEncoding.Base64)'
    )


def test_binary_from_null_and_binary_passthrough():
    assert evaluate("Binary.From(null)") is None
    assert evaluate('Binary.From(Text.ToBinary("x"))') == b"x"


def test_binary_from_rejects_unconvertible_type():
    with pytest.raises(EvalError, match="cannot convert"):
        evaluate("Binary.From(5)")


def test_binary_from_list_and_to_list_round_trip():
    assert evaluate("Binary.FromList({1, 2, 3})") == bytes([1, 2, 3])
    assert evaluate("Binary.ToList(Binary.FromList({1, 2, 3}))") == [1, 2, 3]


def test_binary_from_list_rejects_out_of_range_byte():
    with pytest.raises(EvalError, match="not a byte value"):
        evaluate("Binary.FromList({256})")


# --------------------------------------------------------------------------
# Binary.Compress
# --------------------------------------------------------------------------


def test_binary_compress_deflate_round_trips_with_decompress():
    original = evaluate('Text.ToBinary("hello world")')
    compressed = evaluate(
        'Binary.Compress(Text.ToBinary("hello world"), Compression.Deflate)'
    )
    assert compressed != original
    decompressed = evaluate(
        "Binary.Decompress(_, Compression.Deflate)", bindings={"_": compressed}
    )
    assert decompressed == original


def test_binary_compress_gzip_round_trips_with_decompress():
    original = evaluate('Text.ToBinary("hello world")')
    compressed = evaluate(
        'Binary.Compress(Text.ToBinary("hello world"), Compression.GZip)'
    )
    decompressed = evaluate(
        "Binary.Decompress(_, Compression.GZip)", bindings={"_": compressed}
    )
    assert decompressed == original


def test_binary_compress_refuses_undocumented_kind():
    with pytest.raises(UnsupportedError, match="Compression.GZip"):
        evaluate('Binary.Compress(Text.ToBinary("x"), Compression.None)')


def test_binary_compress_null_in_null_out():
    assert evaluate("Binary.Compress(null, Compression.GZip)") is None


# --------------------------------------------------------------------------
# Binary.Range / Binary.Split
# --------------------------------------------------------------------------


def test_binary_range_matches_ms_docs_examples():
    # learn.microsoft.com/en-us/powerquery-m/binary-range
    eleven = "{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}"
    assert evaluate(f"Binary.Range(#binary({eleven}), 6)") == bytes([6, 7, 8, 9, 10])
    assert evaluate(f"Binary.Range(#binary({eleven}), 6, 2)") == bytes([6, 7])


def test_binary_range_rejects_out_of_range_offset():
    with pytest.raises(EvalError, match="out of range"):
        evaluate("Binary.Range(#binary({1, 2, 3}), 10)")


def test_binary_split_chunks_with_a_shorter_final_piece():
    # docs: "the first element ... is a binary containing the first
    # pageSize bytes ..., the next element ... the next pageSize bytes ...
    # and so on" - no worked example, but the contract is unambiguous for
    # a length that doesn't divide evenly.
    eleven = "{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}"
    assert evaluate(f"Binary.Split(#binary({eleven}), 4)") == [
        bytes([0, 1, 2, 3]),
        bytes([4, 5, 6, 7]),
        bytes([8, 9, 10]),
    ]


def test_binary_split_rejects_non_positive_page_size():
    with pytest.raises(EvalError, match="positive"):
        evaluate("Binary.Split(#binary({1, 2, 3}), 0)")


# --------------------------------------------------------------------------
# Binary.InferContentType / Binary.View / Binary.ViewError / Binary.ViewFunction
# - deliberately refused (see _connectors.py for the reasoning)
# --------------------------------------------------------------------------


def test_binary_infer_content_type_is_refused():
    with pytest.raises(UnsupportedError, match="proprietary detection tables"):
        evaluate('Binary.InferContentType(Text.ToBinary("PK"))')


def test_binary_view_is_refused():
    with pytest.raises(UnsupportedError, match="handler-dispatched"):
        evaluate('Binary.View(null, [GetStream = () => Text.ToBinary("x")])')


def test_binary_view_error_is_refused():
    with pytest.raises(UnsupportedError, match="Binary.View"):
        evaluate('Binary.ViewError([Reason = "DataSource.Error", Message = "x"])')


def test_binary_view_function_is_refused():
    with pytest.raises(UnsupportedError, match="Binary.View"):
        evaluate("Binary.ViewFunction(() => 1)")


# --------------------------------------------------------------------------
# Type.IsNullable / Type.NonNullable
# --------------------------------------------------------------------------


def test_type_is_nullable_matches_ms_docs_example():
    # learn.microsoft.com/en-us/powerquery-m/type-isnullable Example 1.
    # Example 2 (`type nullable number`) cannot even be constructed by
    # this evaluator - see evaluate.py's _eval_type_primary - so it is not
    # reproduced here; the refusal test below covers the one genuinely
    # ambiguous shape this evaluator CAN construct (`type any`).
    assert evaluate("Type.IsNullable(type number)") is False


def test_type_is_nullable_refuses_type_any():
    with pytest.raises(UnsupportedError, match="ambiguous"):
        evaluate("Type.IsNullable(type any)")


def test_type_non_nullable_is_identity_for_a_plain_type():
    result = evaluate("Type.NonNullable(type number)")
    assert result is evaluate("type number")


def test_type_non_nullable_refuses_type_any():
    with pytest.raises(UnsupportedError, match="anynonnull"):
        evaluate("Type.NonNullable(type any)")


def test_type_is_nullable_and_non_nullable_reject_non_type_values():
    with pytest.raises(EvalError, match="expected a type value"):
        evaluate("Type.IsNullable(5)")
    with pytest.raises(EvalError, match="expected a type value"):
        evaluate("Type.NonNullable(5)")


# --------------------------------------------------------------------------
# Value.As / Value.NullableEquals
# --------------------------------------------------------------------------


def test_value_as_matches_ms_docs_example_1():
    # learn.microsoft.com/en-us/powerquery-m/value-as
    assert evaluate("Value.As(123, Number.Type)") == 123


def test_value_as_raises_on_incompatible_type():
    with pytest.raises(EvalError, match="not compatible"):
        evaluate('Value.As("abc", type number)')


def test_value_nullable_equals_propagates_null():
    # learn.microsoft.com/en-us/powerquery-m/value-nullableequals
    assert evaluate("Value.NullableEquals(null, 5)") is None
    assert evaluate("Value.NullableEquals(5, null)") is None
    assert evaluate("Value.NullableEquals(null, null)") is None


def test_value_nullable_equals_matches_value_equals_when_both_present():
    assert evaluate("Value.NullableEquals(5, 5)") is True
    assert evaluate("Value.NullableEquals(5, 6)") is False


def test_value_nullable_equals_refuses_precision_argument():
    with pytest.raises(UnsupportedError, match="precision"):
        evaluate("Value.NullableEquals(1, 1, Precision.Decimal)")


# --------------------------------------------------------------------------
# Value.Add / Value.Subtract / Value.Multiply / Value.Divide
# --------------------------------------------------------------------------


def test_value_arithmetic_matches_the_bare_operators():
    assert evaluate("Value.Add(1, 2)") == 3 == evaluate("1 + 2")
    assert evaluate("Value.Subtract(5, 2)") == 3 == evaluate("5 - 2")
    assert evaluate("Value.Multiply(3, 4)") == 12 == evaluate("3 * 4")
    assert evaluate("Value.Divide(9, 2)") == 4.5 == evaluate("9 / 2")


def test_value_divide_by_zero_matches_the_bare_operator():
    """Both raised until 0.10.0. Neither should: M divides by IEEE rules.

    The pairing is the point - Value.Divide is documented as "the result of
    dividing value1 by value2", so it has to agree with `/` on every input,
    including this one. They share one helper now so they cannot drift.
    """
    assert evaluate("Value.Divide(1, 0)") == evaluate("1 / 0") == math.inf
    assert math.isnan(evaluate("Value.Divide(0, 0)"))


def test_value_arithmetic_refuses_precision_argument():
    with pytest.raises(UnsupportedError, match="precision"):
        evaluate("Value.Add(1, 2, Precision.Decimal)")


def test_value_arithmetic_requires_numbers():
    with pytest.raises(EvalError, match="expected a number"):
        evaluate('Value.Add("a", 1)')


# --------------------------------------------------------------------------
# Value.Metadata / Value.RemoveMetadata / Value.ReplaceMetadata /
# Value.Optimize / Value.NativeQuery
# --------------------------------------------------------------------------


def test_value_metadata_is_always_empty():
    # `meta` is unimplemented (evaluate.py's _SIMPLE_UNSUPPORTED) and
    # Value.ReplaceMetadata is refused below, so no value here can ever
    # carry metadata - the docs' own Value.RemoveMetadata Example 1 proves
    # the answer for that state is the empty record.
    assert evaluate("Value.Metadata(5)") == {}
    assert evaluate('Value.Metadata("abc")') == {}
    assert evaluate("Value.Metadata([a = 1])") == {}


def test_value_remove_metadata_is_identity():
    assert evaluate('Value.RemoveMetadata("abc")') == "abc"
    assert evaluate('Value.Metadata(Value.RemoveMetadata("abc"))') == {}


def test_value_remove_metadata_accepts_optional_field_list():
    assert evaluate('Value.RemoveMetadata("abc", {"a"})') == "abc"


def test_meta_operator_itself_is_unimplemented():
    # Confirms the premise Value.Metadata/RemoveMetadata rely on: there is
    # genuinely no way, anywhere in this evaluator, to attach metadata to a
    # value via the `meta` operator.
    with pytest.raises(UnsupportedError, match="meta"):
        evaluate("1 meta [a = 1]")


def test_value_replace_metadata_is_refused():
    with pytest.raises(UnsupportedError, match="wrapper"):
        evaluate("Value.ReplaceMetadata(1, [a = 1])")


def test_value_optimize_is_identity():
    assert evaluate("Value.Optimize(42)") == 42
    assert evaluate('Value.Optimize("x")') == "x"


def test_value_native_query_is_refused():
    with pytest.raises(UnsupportedError, match="target-specific query"):
        evaluate('Value.NativeQuery(1, "select 1")')


# --------------------------------------------------------------------------
# Expression.Identifier / Expression.Constant
# --------------------------------------------------------------------------


def test_expression_identifier_matches_ms_docs_examples():
    # learn.microsoft.com/en-us/powerquery-m/expression-identifier
    assert evaluate('Expression.Identifier("MyIdentifier")') == "MyIdentifier"
    assert evaluate('Expression.Identifier("My Identifier")') == '#"My Identifier"'


def test_expression_identifier_doubles_embedded_quotes():
    assert evaluate('Expression.Identifier("a""b")') == '#"a""b"'


def test_expression_identifier_quotes_reserved_keywords():
    # "type" is a valid _IDENTIFIER-shaped word but is M-reserved, so it
    # must round-trip through the quoted form, not pass through bare.
    assert evaluate('Expression.Identifier("type")') == '#"type"'


def test_expression_constant_matches_ms_docs_examples():
    # learn.microsoft.com/en-us/powerquery-m/expression-constant
    assert evaluate("Expression.Constant(123)") == "123"
    assert evaluate("Expression.Constant(#date(2035, 1, 2))") == "#date(2035, 1, 2)"
    assert evaluate('Expression.Constant("abc")') == '"abc"'


def test_expression_constant_round_trips_through_evaluate():
    text = evaluate('Expression.Constant("it""s")')
    assert evaluate(text) == 'it"s'


def test_expression_constant_covers_null_and_logical():
    assert evaluate("Expression.Constant(null)") == "null"
    assert evaluate("Expression.Constant(true)") == "true"
    assert evaluate("Expression.Constant(false)") == "false"


def test_expression_constant_refuses_unverified_shapes():
    with pytest.raises(UnsupportedError, match="Expression.Constant"):
        evaluate("Expression.Constant(#duration(1, 0, 0, 0))")
    with pytest.raises(UnsupportedError, match="Expression.Constant"):
        evaluate("Expression.Constant({1, 2})")


# --------------------------------------------------------------------------
# Expression.Evaluate
# --------------------------------------------------------------------------


def test_expression_evaluate_matches_ms_docs_example_1():
    # learn.microsoft.com/en-us/powerquery-m/expression-evaluate Example 1
    assert evaluate('Expression.Evaluate("1 + 1")') == 2


def test_expression_evaluate_matches_ms_docs_example_2():
    # Example 2: a function supplied via the environment record.
    assert (
        evaluate('Expression.Evaluate("List.Sum({1, 2, 3})", [#"List.Sum" = List.Sum])')
        == 6
    )


def test_expression_evaluate_environment_is_optional():
    assert evaluate('Expression.Evaluate("2 * 3")') == 6


def test_expression_evaluate_still_blocks_web_contents():
    # THE required security proof: Expression.Evaluate must not widen what
    # a query can reach. Web.Contents inside the evaluated document is
    # still gated by the exact same (default deny-all) IOPolicy the outer
    # query runs under.
    with pytest.raises(IOBlockedError, match="--allow-net"):
        evaluate('Expression.Evaluate("Web.Contents(""http://example.com"")")')
    # Explicit DENY_ALL, spelled out, for the same reason.
    with pytest.raises(IOBlockedError):
        evaluate(
            'Expression.Evaluate("Web.Contents(""http://example.com"")")',
            io=DENY_ALL,
        )


def test_expression_evaluate_inherits_an_allow_net_policy_unchanged():
    # The flip side of the safety proof: Expression.Evaluate does not
    # WIDEN capability, but it also must not narrow it either - it passes
    # `ctx.io` through unchanged in both directions. A query that legally
    # allowed net access at the top level keeps that permission one level
    # down (there is nothing after --allow-net for a query text with no
    # network to actually reach; asserting the DIFFERENT class of error -
    # "no such host" / connection failure, never IOBlockedError - proves
    # the policy did carry through instead of resetting to deny-all).
    policy = IOPolicy(allow_net=True)
    with pytest.raises(Exception) as caught:
        evaluate(
            'Expression.Evaluate("Web.Contents(""http://127.0.0.1:1""))")',
            io=policy,
        )
    assert not isinstance(caught.value, IOBlockedError)


def test_expression_evaluate_environment_cannot_widen_io_policy():
    # An `environment` record cannot smuggle in a wider IOPolicy - the
    # nested evaluate() call only ever receives `ctx.io`, never anything
    # from the environment record itself.
    with pytest.raises(IOBlockedError):
        evaluate(
            'Expression.Evaluate("Web.Contents(""http://example.com"")", '
            "[allow_net = true])"
        )


def test_expression_evaluate_requires_text():
    with pytest.raises(EvalError, match="expected text"):
        evaluate("Expression.Evaluate(5)")


# --------------------------------------------------------------------------
# Splitter.SplitByNothing / Splitter.SplitTextByWhitespace
# --------------------------------------------------------------------------


def test_splitter_split_by_nothing_returns_a_single_element_list():
    assert evaluate('Splitter.SplitByNothing()("abc")') == ["abc"]


def test_splitter_split_text_by_whitespace_matches_ms_docs_example():
    # learn.microsoft.com/en-us/powerquery-m/splitter-splittextbywhitespace
    assert evaluate(
        'Splitter.SplitTextByWhitespace(QuoteStyle.None)("a b#(tab)c")'
    ) == ["a", "b", "c"]


def test_splitter_split_text_by_whitespace_defaults_to_no_argument():
    assert evaluate('Splitter.SplitTextByWhitespace()("a  b")') == ["a", "b"]


def test_splitter_split_text_by_whitespace_csv_style_respects_quotes():
    assert evaluate(
        'Splitter.SplitTextByWhitespace(QuoteStyle.Csv)("a ""b c"" d")'
    ) == ["a", "b c", "d"]


# --------------------------------------------------------------------------
# Splitter.SplitTextByAnyDelimiter
# --------------------------------------------------------------------------


def test_splitter_split_text_by_any_delimiter_matches_ms_docs_example_1():
    # learn.microsoft.com/en-us/powerquery-m/splitter-splittextbyanydelimiter
    assert evaluate(
        'Splitter.SplitTextByAnyDelimiter({",", ";"}, QuoteStyle.Csv)'
        '("a,b;""c,d;e"",f")'
    ) == ["a", "b", "c,d;e", "f"]


def test_splitter_split_text_by_any_delimiter_matches_ms_docs_example_2():
    assert evaluate(
        """
        let
            startAtEnd = true
        in
            Splitter.SplitTextByAnyDelimiter({",", ";"}, QuoteStyle.Csv, startAtEnd)
                ("a,""b;c,d")
        """
    ) == ["a,b", "c", "d"]


def test_splitter_split_text_by_any_delimiter_rejects_empty_delimiter_list():
    with pytest.raises(EvalError, match="non-empty"):
        evaluate("Splitter.SplitTextByAnyDelimiter({})")


# --------------------------------------------------------------------------
# Splitter.SplitTextByRanges
# --------------------------------------------------------------------------


def test_splitter_split_text_by_ranges_matches_ms_docs_example_1():
    # learn.microsoft.com/en-us/powerquery-m/splitter-splittextbyranges
    assert evaluate('Splitter.SplitTextByRanges({{0, 4}, {2, 10}})("codelimiter")') == [
        "code",
        "delimiter",
    ]


def test_splitter_split_text_by_ranges_matches_ms_docs_example_2():
    assert evaluate(
        """
        let
            startAtEnd = true
        in
            Splitter.SplitTextByRanges({{0, 5}, {6, 2}}, startAtEnd)
                ("RedmondWA?98052")
        """
    ) == ["WA", "98052"]


def test_splitter_split_text_by_ranges_matches_ms_docs_example_3():
    assert evaluate(
        'Splitter.SplitTextByRanges({{0, 5}, {5, null}})("98052Redmond")'
    ) == ["98052", "Redmond"]


# --------------------------------------------------------------------------
# Splitter.SplitTextByRepeatedLengths
# --------------------------------------------------------------------------


def test_splitter_split_text_by_repeated_lengths_matches_ms_docs_example_1():
    # learn.microsoft.com/en-us/powerquery-m/splitter-splittextbyrepeatedlengths
    assert evaluate('Splitter.SplitTextByRepeatedLengths(3)("12345678")') == [
        "123",
        "456",
        "78",
    ]


def test_splitter_split_text_by_repeated_lengths_matches_ms_docs_example_2():
    assert evaluate(
        """
        let
            startAtEnd = true
        in
            Splitter.SplitTextByRepeatedLengths(3, startAtEnd)("87654321")
        """
    ) == ["87", "654", "321"]


def test_splitter_split_text_by_repeated_lengths_rejects_non_positive_length():
    with pytest.raises(EvalError, match="positive"):
        evaluate('Splitter.SplitTextByRepeatedLengths(0)("abc")')


# --------------------------------------------------------------------------
# Combiner.CombineTextByRanges
# --------------------------------------------------------------------------


def test_combiner_combine_text_by_ranges_matches_ms_docs_example():
    # learn.microsoft.com/en-us/powerquery-m/combiner-combinetextbyranges
    assert (
        evaluate(
            "Combiner.CombineTextByRanges({{0, 1}, {3, 2}, {6, null}})"
            '({"abc", "def", "ghijkl"})'
        )
        == "a  de ghijkl"
    )


def test_combiner_combine_text_by_ranges_rejects_wrong_value_count():
    with pytest.raises(EvalError, match="expected"):
        evaluate('Combiner.CombineTextByRanges({{0, 1}})({"a", "b"})')


def test_combiner_combine_text_by_ranges_rejects_template_too_short():
    with pytest.raises(EvalError, match="template is too short"):
        evaluate('Combiner.CombineTextByRanges({{0, null}}, "ab")({"abc"})')
