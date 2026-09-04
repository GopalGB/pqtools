"""Tests for ``pqtools.builtins._misc``: ``Json.FromValue``, ``Lines.*``,
``Binary.Buffer``/``Binary.Combine``, ``Character.*``, ``Guid.From``,
``Splitter.SplitTextByLengths``, ``Combiner.*``, and ``Record.FieldValues``.

Every assertion pinned to a Microsoft Learn worked example says so in a
comment with the exact number ("docs Example 1", "docs Example 2") so a
future reader can re-check it against the live page rather than trust this
comment forever.
"""

from __future__ import annotations

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Json.FromValue
# --------------------------------------------------------------------------


def test_json_from_value_matches_microsoft_docs_example():
    # learn.microsoft.com/en-us/powerquery-m/json-fromvalue's own example.
    query = """
    Text.FromBinary(Json.FromValue([A = {1, true, "3"}, B = #date(2012, 3, 25)]))
    """
    assert evaluate(query) == '{"A":[1,true,"3"],"B":"2012-03-25"}'


def test_json_from_value_returns_binary():
    assert isinstance(evaluate('Json.FromValue("x")'), bytes)
    assert evaluate('Json.FromValue("x")') == b'"x"'


def test_json_from_value_infinity_and_nan_become_null():
    # "#infinity, -#infinity and #nan are converted to null" (docs, verbatim).
    assert evaluate("Text.FromBinary(Json.FromValue(#nan))") == "null"
    assert evaluate("Text.FromBinary(Json.FromValue(#infinity))") == "null"
    assert evaluate("Text.FromBinary(Json.FromValue(-#infinity))") == "null"


def test_json_from_value_whole_number_float_has_no_trailing_dot():
    # M has one number type; a JSON "2.0" for a value that is exactly 2 would
    # contradict the docs example's own compact-integer style ("1" not "1.0").
    assert evaluate("Text.FromBinary(Json.FromValue(2.0))") == "2"


def test_json_from_value_respects_explicit_encoding():
    assert evaluate("Json.FromValue(true, 1200)") == "true".encode("utf-16-le")


def test_json_from_value_rejects_function_values():
    with pytest.raises(EvalError, match="type or function"):
        evaluate("Json.FromValue(each _)")


def test_json_from_value_rejects_duration():
    # Verified-uncertain corner (see _misc.py's module docstring): refused
    # by name rather than guessed at.
    with pytest.raises(UnsupportedError, match="duration"):
        evaluate("Json.FromValue(#duration(1, 0, 0, 0))")


# --------------------------------------------------------------------------
# Lines.FromText / Lines.FromBinary
# --------------------------------------------------------------------------


def test_lines_from_text_splits_on_cr_lf_and_crlf():
    assert evaluate('Lines.FromText("a\r\nb\nc\rd")') == ["a", "b", "c", "d"]


def test_lines_from_text_include_line_separators():
    assert evaluate('Lines.FromText("a\r\nb", null, true)') == ["a\r\n", "b"]


def test_lines_from_text_quote_style_csv_keeps_embedded_break_and_quotes():
    # Real behaviour verified via web research against Microsoft's own
    # QuoteStyle.Csv worked example: a quoted line break stays inside the
    # SAME output line, and the quote characters are preserved verbatim
    # (Lines.FromText does not do CSV field-unescaping, only line-breaking).
    raw_text = '"line1\r\nline2"\r\nline3'
    m_literal = '"' + raw_text.replace('"', '""') + '"'
    query = f"Lines.FromText({m_literal}, QuoteStyle.Csv)"
    assert evaluate(query) == ['"line1\r\nline2"', "line3"]


def test_lines_from_binary_decodes_then_splits():
    data = list(b"hi\r\nthere")
    query = f"Lines.FromBinary(#binary({{{','.join(map(str, data))}}}))"
    assert evaluate(query) == ["hi", "there"]


def test_lines_from_binary_respects_explicit_encoding():
    data = list("ab".encode("utf-16-le"))
    query = (
        f"Lines.FromBinary(#binary({{{','.join(map(str, data))}}}), null, null, 1200)"
    )
    assert evaluate(query) == ["ab"]


def test_lines_from_binary_rejects_non_binary():
    with pytest.raises(EvalError, match="expected binary"):
        evaluate('Lines.FromBinary("not binary")')


# --------------------------------------------------------------------------
# Lines.ToText / Lines.ToBinary
# --------------------------------------------------------------------------


def test_lines_to_text_matches_microsoft_docs_example():
    query = 'Lines.ToText({"ID,Name", "1,Bob Smith", "2,Jan Lee"})'
    assert evaluate(query) == "ID,Name\r\n1,Bob Smith\r\n2,Jan Lee\r\n"


def test_lines_to_text_custom_separator():
    assert evaluate('Lines.ToText({"a", "b"}, "|")') == "a|b|"


def test_lines_to_binary_default_encoding_and_separator():
    assert evaluate('Lines.ToBinary({"a", "b"})') == b"a\r\nb\r\n"


def test_lines_to_binary_byte_order_mark():
    assert evaluate('Lines.ToBinary({"a"}, "", null, true)') == b"\xef\xbb\xbfa"


def test_lines_to_binary_bom_refused_for_encoding_without_one():
    with pytest.raises(UnsupportedError, match="byte order mark"):
        evaluate('Lines.ToBinary({"a"}, "", 1252, true)')


def test_lines_roundtrip_leaves_a_trailing_empty_line():
    # Lines.ToBinary appends the separator to EVERY line, including the
    # last (docs, verbatim - same wording as Lines.ToText). So the binary
    # it produces ends in a separator, and a default Lines.FromBinary read
    # of that binary sees one more (empty) line after it - not a clean
    # round trip. Pinned here so a future "fix" doesn't silently change it.
    query = 'Lines.FromBinary(Lines.ToBinary({"a", "b", "c"}))'
    assert evaluate(query) == ["a", "b", "c", ""]


# --------------------------------------------------------------------------
# Binary.Buffer / Binary.Combine
# --------------------------------------------------------------------------


def test_binary_buffer_is_identity():
    assert evaluate("Binary.Buffer(#binary({1, 2, 3}))") == bytes([1, 2, 3])


def test_binary_buffer_null_propagates():
    assert evaluate("Binary.Buffer(null)") is None


def test_binary_buffer_rejects_non_binary():
    with pytest.raises(EvalError, match="expected binary"):
        evaluate('Binary.Buffer("x")')


def test_binary_combine_concatenates_in_order():
    query = "Binary.Combine({#binary({1, 2}), #binary({}), #binary({3, 4, 5})})"
    assert evaluate(query) == bytes([1, 2, 3, 4, 5])


def test_binary_combine_empty_list():
    assert evaluate("Binary.Combine({})") == b""


# --------------------------------------------------------------------------
# Character.FromNumber / Character.ToNumber
# --------------------------------------------------------------------------


def test_character_from_number_matches_microsoft_docs_example_1():
    assert evaluate("Character.FromNumber(9)") == "\t"


def test_character_to_number_matches_microsoft_docs_example_1():
    assert evaluate('Character.ToNumber("A")') == 65


def test_character_roundtrip_matches_microsoft_docs_example_2():
    assert evaluate('Character.FromNumber(Character.ToNumber("A"))') == "A"


def test_character_roundtrip_astral_code_point():
    # Docs Example 3's "grinning face" code point, round-tripped without
    # embedding a literal astral character in the M source text.
    assert evaluate("Character.ToNumber(Character.FromNumber(0x1F600))") == 0x1F600


def test_character_null_propagates_both_ways():
    assert evaluate("Character.FromNumber(null)") is None
    assert evaluate("Character.ToNumber(null)") is None


def test_character_to_number_rejects_multi_character_text():
    with pytest.raises(EvalError, match="single character"):
        evaluate('Character.ToNumber("AB")')


# --------------------------------------------------------------------------
# Guid.From
# --------------------------------------------------------------------------

_GUID_CANON = "05fe1dad-c8c2-4f3b-a4c2-d194116b4967"


def test_guid_from_matches_all_four_microsoft_docs_examples():
    assert evaluate('Guid.From("05FE1DADC8C24F3BA4C2D194116B4967")') == _GUID_CANON
    assert evaluate('Guid.From("05FE1DAD-C8C2-4F3B-A4C2-D194116B4967")') == _GUID_CANON
    assert (
        evaluate('Guid.From("{05FE1DAD-C8C2-4F3B-A4C2-D194116B4967}")') == _GUID_CANON
    )
    assert (
        evaluate('Guid.From("(05FE1DAD-C8C2-4F3B-A4C2-D194116B4967)")') == _GUID_CANON
    )


def test_guid_from_null_propagates():
    assert evaluate("Guid.From(null)") is None


def test_guid_from_rejects_bad_format():
    with pytest.raises(EvalError, match="not a recognized Guid"):
        evaluate('Guid.From("not-a-guid")')


# --------------------------------------------------------------------------
# Splitter.SplitTextByLengths
# --------------------------------------------------------------------------


def test_splitter_split_text_by_lengths_matches_microsoft_docs_example_1():
    assert evaluate('Splitter.SplitTextByLengths({2, 3})("AB123")') == ["AB", "123"]


def test_splitter_split_text_by_lengths_matches_microsoft_docs_example_2():
    query = 'Splitter.SplitTextByLengths({5, 2}, true)("RedmondWA98052")'
    assert evaluate(query) == ["WA", "98052"]


def test_splitter_split_text_by_lengths_rejects_negative_length():
    with pytest.raises(EvalError, match="must not be negative"):
        evaluate('Splitter.SplitTextByLengths({-1})("x")')


# --------------------------------------------------------------------------
# Combiner.CombineTextByDelimiter / CombineTextByEachDelimiter
# --------------------------------------------------------------------------


def test_combiner_combine_text_by_delimiter_matches_microsoft_docs_example_1():
    assert evaluate('Combiner.CombineTextByDelimiter(";")({"a", "b", "c"})') == "a;b;c"


def test_combiner_combine_text_by_delimiter_quote_style_csv():
    # Docs Example 2 (via Table.CombineColumns) shows "c" + "d,e,f" combined
    # with a comma delimiter, QuoteStyle.Csv, as c,"d,e,f" (the field
    # containing the delimiter gets quoted). Tested on the returned function
    # directly rather than through Table.CombineColumns.
    query = 'Combiner.CombineTextByDelimiter(",", QuoteStyle.Csv)({"c", "d,e,f"})'
    assert evaluate(query) == 'c,"d,e,f"'


def test_combiner_combine_text_by_delimiter_roundtrips_through_the_splitter():
    # Cross-checked against _table_shape.py's already-implemented, already-
    # tested Splitter.SplitTextByDelimiter rather than trusted blind: the
    # combiner's quoting must be exactly what its paired splitter expects.
    query = """
    Splitter.SplitTextByDelimiter(",", QuoteStyle.Csv)(
        Combiner.CombineTextByDelimiter(",", QuoteStyle.Csv)({"c", "d,e,f", "g\"\"h"})
    )
    """
    assert evaluate(query) == ["c", "d,e,f", 'g"h']


def test_combiner_combine_text_by_delimiter_rejects_empty_delimiter():
    with pytest.raises(EvalError, match="must not be empty"):
        evaluate('Combiner.CombineTextByDelimiter("")({"a"})')


def test_combiner_combine_text_by_each_delimiter_matches_microsoft_docs_example():
    query = 'Combiner.CombineTextByEachDelimiter({"=", "+"})({"a", "b", "c"})'
    assert evaluate(query) == "a=b+c"


def test_combiner_combine_text_by_each_delimiter_roundtrips_through_the_splitter():
    query = """
    Splitter.SplitTextByEachDelimiter({"=", "+"})(
        Combiner.CombineTextByEachDelimiter({"=", "+"})({"a", "b", "c"})
    )
    """
    assert evaluate(query) == ["a", "b", "c"]


def test_combiner_combine_text_by_each_delimiter_rejects_too_few_delimiters():
    with pytest.raises(EvalError, match="delimiter"):
        evaluate('Combiner.CombineTextByEachDelimiter({"="})({"a", "b", "c"})')


# --------------------------------------------------------------------------
# Combiner.CombineTextByPositions / CombineTextByLengths
# --------------------------------------------------------------------------


def test_combiner_combine_text_by_positions_matches_microsoft_docs_example():
    query = 'Combiner.CombineTextByPositions({0, 5, 10})({"abc", "def", "ghi"})'
    assert evaluate(query) == "abc  def  ghi"


def test_combiner_combine_text_by_positions_uses_template_tail():
    query = 'Combiner.CombineTextByPositions({0, 4}, "----------")({"ab", "cd"})'
    assert evaluate(query) == "ab--cd----"


def test_combiner_combine_text_by_positions_rejects_short_template():
    with pytest.raises(EvalError, match="too short"):
        evaluate('Combiner.CombineTextByPositions({0, 5}, "1234")({"ab", "cd"})')


def test_combiner_combine_text_by_lengths_matches_microsoft_docs_example_1():
    query = 'Combiner.CombineTextByLengths({1, 2, 3})({"aaa", "bbb", "ccc"})'
    assert evaluate(query) == "abbccc"


def test_combiner_combine_text_by_lengths_matches_microsoft_docs_example_2():
    query = (
        'Combiner.CombineTextByLengths({1, 2, 3}, "*********")({"aaa", "bbb", "ccc"})'
    )
    assert evaluate(query) == "abbccc***"


def test_combiner_combine_text_by_lengths_rejects_count_mismatch():
    with pytest.raises(EvalError, match="expected"):
        evaluate('Combiner.CombineTextByLengths({1, 2})({"a"})')


# --------------------------------------------------------------------------
# Record.FieldValues
# --------------------------------------------------------------------------


def test_record_field_values_matches_microsoft_docs_example():
    query = 'Record.FieldValues([CustomerID = 1, Name = "Bob", Phone = "123-4567"])'
    assert evaluate(query) == [1, "Bob", "123-4567"]
