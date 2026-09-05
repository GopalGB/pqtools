"""Tests for ``pqtools.builtins._binaryformat``: the whole ``BinaryFormat.*``
namespace (20 names) plus the two enum families it depends on
(``ByteOrder.*``, ``BinaryOccurrence.*``).

Every assertion pinned to a Microsoft Learn worked example says which
example ("docs Example 1", "docs Example 2") so a future reader can
re-check it against the live page rather than trust this comment forever.
Assertions with no such comment are this file's own edge-case coverage
(error paths, endianness pinning, round-trips) - not sourced from the
reference, and said so at the point they matter.
"""

from __future__ import annotations

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Primitives - one byte per row, no byte-order question.
# --------------------------------------------------------------------------


def test_byte_reads_one_unsigned_byte():
    assert evaluate("BinaryFormat.Byte(#binary({200}))") == 200


def test_byte_past_end_of_binary_is_a_named_error():
    with pytest.raises(EvalError, match="BinaryFormat.Byte"):
        evaluate("BinaryFormat.Byte(#binary({}))")


def test_null_reads_zero_bytes_and_returns_null():
    # docs: "reads zero bytes and returns null" - even on an empty binary,
    # since zero bytes are always available.
    assert evaluate("BinaryFormat.Null(#binary({}))") is None
    assert evaluate("BinaryFormat.Null(#binary({9}))") is None


# --------------------------------------------------------------------------
# Endianness - the trap. BinaryFormat.ByteOrder's own page: "The default
# byte order is ByteOrder.BigEndian." Independently confirmed by
# BinaryFormat.Record's own worked example (0x00,0x01 -> 1 as a big-endian
# UnsignedInteger16, with no ByteOrder wrapping at all).
# --------------------------------------------------------------------------


def test_unsigned_integer16_defaults_to_big_endian():
    assert evaluate("BinaryFormat.UnsignedInteger16(#binary({0x01, 0x00}))") == 256


def test_unsigned_integer16_explicit_big_endian_matches_default():
    query = (
        "BinaryFormat.ByteOrder(BinaryFormat.UnsignedInteger16, "
        "ByteOrder.BigEndian)(#binary({0x01, 0x00}))"
    )
    assert evaluate(query) == 256


def test_unsigned_integer16_explicit_little_endian_flips_it():
    query = (
        "BinaryFormat.ByteOrder(BinaryFormat.UnsignedInteger16, "
        "ByteOrder.LittleEndian)(#binary({0x01, 0x00}))"
    )
    assert evaluate(query) == 1


def test_signed_integer16_two_s_complement_big_endian_default():
    assert evaluate("BinaryFormat.SignedInteger16(#binary({0xFF, 0xFF}))") == -1


def test_signed_integer32_two_s_complement_little_endian():
    query = (
        "BinaryFormat.ByteOrder(BinaryFormat.SignedInteger32, "
        "ByteOrder.LittleEndian)(#binary({0xFF, 0xFF, 0xFF, 0xFF}))"
    )
    assert evaluate(query) == -1


def test_unsigned_integer64_big_endian_default():
    data = "#binary({0,0,0,0,0,0,1,0})"
    assert evaluate(f"BinaryFormat.UnsignedInteger64({data})") == 256


def test_byte_order_wraps_a_record_so_every_multi_byte_field_inherits_it():
    # Confirms ByteOrder threads through a compound format, not just a bare
    # primitive - the whole point of wrapping a struct, not one field.
    query = (
        "BinaryFormat.ByteOrder("
        "BinaryFormat.Record([A = BinaryFormat.UnsignedInteger16, "
        "B = BinaryFormat.UnsignedInteger16]), ByteOrder.LittleEndian)"
        "(#binary({0x01, 0x00, 0x02, 0x00}))"
    )
    assert evaluate(query) == {"A": 1, "B": 2}


def test_byte_order_rejects_an_unknown_code():
    with pytest.raises(EvalError, match="BinaryFormat.ByteOrder"):
        evaluate("BinaryFormat.ByteOrder(BinaryFormat.Byte, 5)(#binary({1}))")


def test_single_precision_float_big_endian():
    # struct.pack(">f", 1.5).hex() == "3fc00000" - verified independently.
    assert evaluate("BinaryFormat.Single(#binary({0x3f, 0xc0, 0x00, 0x00}))") == 1.5


def test_double_precision_float_little_endian():
    # struct.pack("<d", 1.5).hex() == "000000000000f83f".
    query = (
        "BinaryFormat.ByteOrder(BinaryFormat.Double, ByteOrder.LittleEndian)"
        "(#binary({0x00,0x00,0x00,0x00,0x00,0x00,0xf8,0x3f}))"
    )
    assert evaluate(query) == 1.5


# --------------------------------------------------------------------------
# Decimal - the one primitive with no worked example on its own reference
# page at all. Pinned against Python's own `decimal` module encoding the
# well-known, decades-stable .NET `System.Decimal.GetBits` layout (4 32-bit
# words: lo, mid, hi, flags; flags packs sign at bit 31 and scale at bits
# 16-23) - see _binaryformat.py's `_decode_decimal` docstring for why this
# is the best-faith reproduction available, not a Microsoft-verified value.
# --------------------------------------------------------------------------


def test_decimal_positive_with_scale_big_endian():
    # 123.45: mantissa 12345, scale 2, sign 0.
    data = "#binary({0x00,0x00,0x30,0x39, 0,0,0,0, 0,0,0,0, 0x00,0x02,0x00,0x00})"
    assert evaluate(f"BinaryFormat.Decimal({data})") == 123.45


def test_decimal_negative_whole_number_big_endian():
    # -2: mantissa 2, scale 0, sign 1 -> flags = 0x80000000.
    data = "#binary({0,0,0,2, 0,0,0,0, 0,0,0,0, 0x80,0,0,0})"
    assert evaluate(f"BinaryFormat.Decimal({data})") == -2


def test_decimal_scale_over_28_is_a_named_error():
    data = "#binary({0,0,0,1, 0,0,0,0, 0,0,0,0, 0x00,0x1D,0x00,0x00})"  # scale 29
    with pytest.raises(EvalError, match="BinaryFormat.Decimal"):
        evaluate(f"BinaryFormat.Decimal({data})")


# --------------------------------------------------------------------------
# BinaryFormat.Binary
# --------------------------------------------------------------------------


def test_binary_with_no_length_reads_the_remainder():
    assert evaluate("BinaryFormat.Binary()(#binary({1,2,3}))") == bytes([1, 2, 3])


def test_binary_with_fixed_length():
    assert evaluate("BinaryFormat.Binary(2)(#binary({1,2,3}))") == bytes([1, 2])


def test_binary_with_length_prefix_format():
    query = "BinaryFormat.Binary(BinaryFormat.Byte)(#binary({2, 9, 8, 7}))"
    assert evaluate(query) == bytes([9, 8])


# --------------------------------------------------------------------------
# BinaryFormat.Choice - docs Examples 1 and 2 (Example 3 needs `type list`,
# which this evaluator cannot even construct as a value - see below).
# --------------------------------------------------------------------------


def test_choice_docs_example_1():
    query = """
    let
        binaryData = #binary({2, 3, 4, 5}),
        listFormat = BinaryFormat.Choice(
            BinaryFormat.Byte,
            (length) => BinaryFormat.List(BinaryFormat.Byte, length)
        )
    in
        listFormat(binaryData)
    """
    assert evaluate(query) == [3, 4]


def test_choice_docs_example_2_with_combine_via_record():
    query = """
    let
        binaryData = #binary({2, 3, 4, 5}),
        listFormat = BinaryFormat.Choice(
            BinaryFormat.Byte,
            (length) => BinaryFormat.Record([
                length = length,
                list = BinaryFormat.List(BinaryFormat.Byte, length)
            ])
        )
    in
        listFormat(binaryData)
    """
    assert evaluate(query) == {"length": 2, "list": [3, 4]}


def test_choice_with_a_real_combine_function():
    # Not one of the two docs examples (neither uses combineFunction) - this
    # exercises the fourth, documented-but-unillustrated parameter directly.
    query = """
    BinaryFormat.Choice(
        BinaryFormat.Byte,
        (first) => BinaryFormat.Byte,
        type any,
        (first, second) => first + second
    )(#binary({2, 5}))
    """
    assert evaluate(query) == 7


def test_choice_accepts_the_documented_type_list_streaming_hint():
    """This asserted `type list` could not be constructed here at all.

    True when it was written: only 11 of the M specification's 18 primitive
    type names were registered, and `list` was not one of them - so
    BinaryFormat.Choice's own documented example failed one level above this
    module with "type value: type list", as though a keyword of the language
    were a misspelling. All 18 are registered now, so the example runs.

    The `type` argument remains a streaming hint that changes no result the
    page's Example 3 can distinguish, so it is accepted and not acted on.
    """
    query = """
    BinaryFormat.Choice(
        BinaryFormat.Byte,
        (length) => BinaryFormat.List(BinaryFormat.Byte, length),
        type list
    )
    """
    assert evaluate(query) is not None


def test_choice_choose_function_must_return_a_format():
    query = "BinaryFormat.Choice(BinaryFormat.Byte, (x) => x)(#binary({1}))"
    with pytest.raises(UnsupportedError, match="BinaryFormat.Choice"):
        evaluate(query)


# --------------------------------------------------------------------------
# BinaryFormat.Group - docs Examples 1 and 2.
# --------------------------------------------------------------------------

_GROUP_EXAMPLE_1 = """
let
    b = #binary({
        1, 11,
        2, 22,
        2, 22,
        5, 55,
        1, 11
    }),
    f = BinaryFormat.Group(
        BinaryFormat.Byte,
        {
            {1, BinaryFormat.Byte, BinaryOccurrence.Required},
            {2, BinaryFormat.Byte, BinaryOccurrence.Repeating},
            {3, BinaryFormat.Byte, BinaryOccurrence.Optional},
            {4, BinaryFormat.Byte, BinaryOccurrence.Repeating}
        },
        (extra) => BinaryFormat.Byte
    )
in
    f(b)
"""

_GROUP_EXAMPLE_2 = """
let
    b = #binary({
        1, 101,
        1, 102
    }),
    f = BinaryFormat.Group(
        BinaryFormat.Byte,
        {
            {1, BinaryFormat.Byte, BinaryOccurrence.Repeating,
              0, (list) => List.Sum(list)},
            {2, BinaryFormat.Byte, BinaryOccurrence.Optional, 123}
        }
    )
in
    f(b)
"""


def test_group_docs_example_1_required_repeating_optional_and_extra():
    assert evaluate(_GROUP_EXAMPLE_1) == [11, [22, 22], None, []]


def test_group_docs_example_2_transform_and_given_default():
    assert evaluate(_GROUP_EXAMPLE_2) == [203, 123]


def test_group_required_key_missing_is_a_named_error():
    query = """
    let
        b = #binary({2, 22}),
        f = BinaryFormat.Group(
            BinaryFormat.Byte,
            {{1, BinaryFormat.Byte, BinaryOccurrence.Required},
             {2, BinaryFormat.Byte, BinaryOccurrence.Optional}}
        )
    in
        f(b)
    """
    with pytest.raises(EvalError, match="BinaryFormat.Group"):
        evaluate(query)


def test_group_unexpected_key_without_extra_is_a_named_error():
    query = """
    let
        b = #binary({9, 99}),
        f = BinaryFormat.Group(
            BinaryFormat.Byte,
            {{1, BinaryFormat.Byte, BinaryOccurrence.Optional}}
        )
    in
        f(b)
    """
    with pytest.raises(EvalError, match="unexpected key"):
        evaluate(query)


def test_group_duplicate_key_definitions_are_rejected():
    query = """
    BinaryFormat.Group(
        BinaryFormat.Byte,
        {{1, BinaryFormat.Byte, BinaryOccurrence.Optional},
         {1, BinaryFormat.Byte, BinaryOccurrence.Optional}}
    )
    """
    with pytest.raises(EvalError, match="duplicate key"):
        evaluate(query)


def test_group_zero_length_key_and_value_format_would_never_terminate():
    # BinaryFormat.Null never advances the cursor. When BOTH the key format
    # and the matched item's format are zero-length, one full key/value
    # cycle makes no progress at all, and reading key/value pairs until
    # end-of-data would hang forever without this guard. (A zero-length KEY
    # paired with a byte-consuming VALUE is not a hang - the value read
    # still advances the cursor - so this pins the case that actually would.)
    query = """
    BinaryFormat.Group(
        BinaryFormat.Null,
        {{null, BinaryFormat.Null, BinaryOccurrence.Repeating}}
    )(#binary({1}))
    """
    with pytest.raises(EvalError, match="never terminate"):
        evaluate(query)


def test_group_last_key_parameter_is_refused_by_name():
    query = """
    BinaryFormat.Group(
        BinaryFormat.Byte,
        {{1, BinaryFormat.Byte, BinaryOccurrence.Optional}},
        null,
        1
    )
    """
    with pytest.raises(UnsupportedError, match="lastKey"):
        evaluate(query)


# --------------------------------------------------------------------------
# BinaryFormat.Length - docs Examples 1 and 2.
# --------------------------------------------------------------------------


def test_length_docs_example_1_fixed_count():
    query = """
    let
        binaryData = #binary({1, 2, 3}),
        listFormat = BinaryFormat.Length(BinaryFormat.List(BinaryFormat.Byte), 2)
    in
        listFormat(binaryData)
    """
    assert evaluate(query) == [1, 2]


def test_length_docs_example_2_length_prefix_format():
    query = """
    let
        binaryData = #binary({1, 2, 3}),
        listFormat = BinaryFormat.Length(
            BinaryFormat.List(BinaryFormat.Byte),
            BinaryFormat.Byte
        )
    in
        listFormat(binaryData)
    """
    assert evaluate(query) == [2]


def test_length_past_end_of_binary_is_a_named_error():
    query = "BinaryFormat.Length(BinaryFormat.Binary(), 5)(#binary({1,2}))"
    with pytest.raises(EvalError, match="BinaryFormat.Length"):
        evaluate(query)


# --------------------------------------------------------------------------
# BinaryFormat.List - docs Examples 1, 2, 3.
# --------------------------------------------------------------------------


def test_list_docs_example_1_reads_to_end():
    query = """
    let
        binaryData = #binary({1, 2, 3}),
        listFormat = BinaryFormat.List(BinaryFormat.Byte)
    in
        listFormat(binaryData)
    """
    assert evaluate(query) == [1, 2, 3]


def test_list_docs_example_2_fixed_count():
    query = """
    let
        binaryData = #binary({1, 2, 3}),
        listFormat = BinaryFormat.List(BinaryFormat.Byte, 2)
    in
        listFormat(binaryData)
    """
    assert evaluate(query) == [1, 2]


def test_list_docs_example_3_predicate_includes_the_stopping_item():
    query = """
    let
        binaryData = #binary({1, 2, 3}),
        listFormat = BinaryFormat.List(BinaryFormat.Byte, (x) => x < 2)
    in
        listFormat(binaryData)
    """
    assert evaluate(query) == [1, 2]


def test_list_count_as_length_prefix_format():
    # Documented in prose ("If countOrCondition is a binary format, the
    # count of items is expected to precede the list") but not shown in any
    # of the page's three examples - this is that fourth, unillustrated
    # shape, analogous to BinaryFormat.Length's own Example 2.
    query = (
        "BinaryFormat.List(BinaryFormat.Byte, BinaryFormat.Byte)(#binary({2, 9, 8, 7}))"
    )
    assert evaluate(query) == [9, 8]


def test_list_negative_count_is_a_named_error():
    with pytest.raises(EvalError, match="negative"):
        evaluate("BinaryFormat.List(BinaryFormat.Byte, -1)(#binary({1,2}))")


def test_list_zero_length_item_format_with_no_count_would_never_terminate():
    with pytest.raises(EvalError, match="never terminate"):
        evaluate("BinaryFormat.List(BinaryFormat.Null)(#binary({1}))")


def test_list_predicate_must_return_a_logical_value():
    query = "BinaryFormat.List(BinaryFormat.Byte, (x) => x)(#binary({1,2}))"
    with pytest.raises(EvalError, match="logical value"):
        evaluate(query)


# --------------------------------------------------------------------------
# BinaryFormat.Record - docs' one example.
# --------------------------------------------------------------------------


def test_record_docs_example():
    query = """
    let
        binaryData = #binary({
            0x00, 0x01,
            0x00, 0x00, 0x00, 0x02
        }),
        recordFormat = BinaryFormat.Record([
            A = BinaryFormat.UnsignedInteger16,
            B = BinaryFormat.UnsignedInteger32
        ])
    in
        recordFormat(binaryData)
    """
    assert evaluate(query) == {"A": 1, "B": 2}


def test_record_echoes_a_non_format_field_without_reading_any_bytes():
    # "If a field contains a value that is not a binary format value, then
    # no data is read for that field, and the field value is echoed to the
    # result" - verbatim from the page, not exercised by its own example.
    query = """
    BinaryFormat.Record([Tag = "fixed", Value = BinaryFormat.Byte])
    (#binary({42}))
    """
    assert evaluate(query) == {"Tag": "fixed", "Value": 42}


# --------------------------------------------------------------------------
# BinaryFormat.Text - docs Examples 1 and 2, plus the prose-only (no
# worked example) BOM-sniffing default path.
# --------------------------------------------------------------------------


def test_text_docs_example_1_fixed_length_ascii():
    query = """
    let
        binaryData = #binary({65, 66, 67}),
        textFormat = BinaryFormat.Text(2, TextEncoding.Ascii)
    in
        textFormat(binaryData)
    """
    assert evaluate(query) == "AB"


def test_text_docs_example_2_length_prefix_ascii():
    query = """
    let
        binaryData = #binary({2, 65, 66}),
        textFormat = BinaryFormat.Text(BinaryFormat.Byte, TextEncoding.Ascii)
    in
        textFormat(binaryData)
    """
    assert evaluate(query) == "AB"


def test_text_defaults_to_utf8_with_no_byte_order_mark():
    query = 'BinaryFormat.Text(3)(Text.ToBinary("caf", TextEncoding.Utf8))'
    assert evaluate(query) == "caf"


def test_text_sniffs_a_utf8_byte_order_mark():
    query = "BinaryFormat.Text(6)(#binary({0xEF, 0xBB, 0xBF, 0x41, 0x42, 0x43}))"
    assert evaluate(query) == "ABC"


def test_text_sniffs_a_utf16_le_byte_order_mark():
    data = "#binary({0xFF, 0xFE, 0x41, 0x00, 0x42, 0x00})"
    assert evaluate(f"BinaryFormat.Text(6)({data})") == "AB"


def test_text_sniffs_a_utf16_be_byte_order_mark():
    data = "#binary({0xFE, 0xFF, 0x00, 0x41, 0x00, 0x42})"
    assert evaluate(f"BinaryFormat.Text(6)({data})") == "AB"


def test_text_unknown_code_page_is_refused_by_name():
    query = "BinaryFormat.Text(1, 999999)"
    with pytest.raises(UnsupportedError, match="BinaryFormat.Text"):
        evaluate(query)


def test_text_undecodable_bytes_is_a_named_error():
    query = "BinaryFormat.Text(1, TextEncoding.Ascii)(#binary({200}))"
    with pytest.raises(EvalError, match="BinaryFormat.Text"):
        evaluate(query)


# --------------------------------------------------------------------------
# BinaryFormat.Transform - docs' one example.
# --------------------------------------------------------------------------


def test_transform_docs_example():
    query = """
    let
        binaryData = #binary({1}),
        transformFormat = BinaryFormat.Transform(
            BinaryFormat.Byte,
            (x) => x + 1
        )
    in
        transformFormat(binaryData)
    """
    assert evaluate(query) == 2


# --------------------------------------------------------------------------
# Cross-cutting: a format built from something other than a BinaryFormat.*
# value is refused, never silently mishandled.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        "BinaryFormat.List((x) => x)",
        "BinaryFormat.Transform((x) => x, (y) => y)",
        "BinaryFormat.Length((x) => x, 1)",
        "BinaryFormat.ByteOrder((x) => x, ByteOrder.BigEndian)",
    ],
)
def test_a_non_format_item_argument_is_refused_by_name(factory):
    # A bare M lambda where a BinaryFormat.* value is required: this
    # evaluator has no way to learn how many bytes an arbitrary function
    # consumed, so every combinator refuses it by name (`_as_format`)
    # instead of guessing.
    with pytest.raises(UnsupportedError, match="BinaryFormat"):
        evaluate(factory)


def test_binary_format_byte_bare_reference_is_itself_callable():
    # The whole point of the combinator model: BinaryFormat.Byte need not be
    # invoked to be passed around - it IS the format value.
    assert evaluate("BinaryFormat.List(BinaryFormat.Byte, 1)(#binary({7}))") == [7]


def test_the_two_enum_families_binaryformat_depends_on_resolve():
    assert evaluate("ByteOrder.LittleEndian") == 0
    assert evaluate("ByteOrder.BigEndian") == 1
    assert evaluate("BinaryOccurrence.Optional") == 0
    assert evaluate("BinaryOccurrence.Required") == 1
    assert evaluate("BinaryOccurrence.Repeating") == 2
