"""Tests for the 0.5.0 scalar-builtin expansion: ``Text.*``, ``List.*``,
``Number.*``, ``Record.*`` (see PRD-0.5.0-builtins.md P3). Every new
function gets at least one test here; every documented edge case this
session had to reason about ("trap") gets an explicit pinning test, called
out in a comment quoting the real Power Query behaviour it was verified
against.
"""

import math
import re

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Text.*
# --------------------------------------------------------------------------


def test_text_pad_start_and_end():
    assert evaluate('Text.PadStart("Name", 10)') == "      Name"
    assert evaluate('Text.PadStart("Name", 10, "|")') == "||||||Name"
    assert evaluate('Text.PadEnd("Name", 10, "|")') == "Name||||||"


def test_text_pad_does_not_truncate_when_already_long_enough():
    # count < len(text) -> the text is returned unchanged, never truncated.
    assert evaluate('Text.PadStart("abcdef", 3)') == "abcdef"
    assert evaluate('Text.PadEnd("abcdef", 3)') == "abcdef"


def test_text_pad_null_propagates():
    assert evaluate("Text.PadStart(null, 5)") is None
    assert evaluate("Text.PadEnd(null, 5)") is None


def test_text_pad_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate('Text.PadStart("x", -1)')


def test_text_middle_matches_ms_docs_examples():
    assert evaluate('Text.Middle("Hello World", 6, 5)') == "World"
    # Trap: an over-long count CLAMPS to the end of the text, it does not
    # throw (verified against the MS docs' own example 2).
    assert evaluate('Text.Middle("Hello World", 6, 20)') == "World"
    assert evaluate('Text.Middle("Hello World", 0, 2)') == "He"


def test_text_middle_no_count_goes_to_end():
    assert evaluate('Text.Middle("Hello World", 6)') == "World"


def test_text_middle_start_past_end_returns_empty_not_error():
    assert evaluate('Text.Middle("hi", 50)') == ""


def test_text_middle_null_propagates():
    assert evaluate("Text.Middle(null, 0)") is None


def test_text_before_delimiter_not_found_returns_whole_text():
    # Trap (verified against real PQ): Text.BeforeDelimiter returns the
    # WHOLE original text when the delimiter is missing - it does not
    # throw and does not return "".
    assert evaluate('Text.BeforeDelimiter("no dash here", "-")') == "no dash here"


def test_text_before_delimiter_occurrence_index():
    assert evaluate('Text.BeforeDelimiter("111-222-333", "-")') == "111"
    assert evaluate('Text.BeforeDelimiter("111-222-333", "-", 1)') == "111-222"


def test_text_after_delimiter_not_found_returns_empty():
    # Trap (verified): Text.AfterDelimiter is NOT the mirror image of
    # Text.BeforeDelimiter - on a missing delimiter it returns "", not the
    # whole text.
    assert evaluate('Text.AfterDelimiter("no dash here", "-")') == ""
    assert evaluate('Text.AfterDelimiter("111-222-333", "-")') == "222-333"


def test_text_between_delimiters_matches_ms_docs_example():
    src = 'Text.BetweenDelimiters("111 (222) 333 (444)", "(", ")")'
    assert evaluate(src) == "222"
    src2 = 'Text.BetweenDelimiters("111 (222) 333 (444)", "(", ")", 1, 0)'
    assert evaluate(src2) == "444"


def test_text_between_delimiters_not_found_returns_empty():
    assert evaluate('Text.BetweenDelimiters("plain text", "[", "]")') == ""


def test_text_select_and_remove():
    assert evaluate('Text.Select("a,b;c", {"a","b","c"})') == "abc"
    assert evaluate('Text.Remove("a,b;c", {",", ";"})') == "abc"
    # A plain text argument is also accepted as "its characters".
    assert evaluate('Text.Select("a1b2c3", "abc")') == "abc"


def test_text_repeat():
    assert evaluate('Text.Repeat("a", 5)') == "aaaaa"
    assert evaluate('Text.Repeat("ab", 0)') == ""


def test_text_reverse():
    assert evaluate('Text.Reverse("123")') == "321"


def test_text_starts_with_ends_with():
    assert evaluate('Text.StartsWith("Hello, World", "hello")') is False
    assert evaluate('Text.StartsWith("Hello, World", "Hello")') is True
    assert evaluate('Text.EndsWith("Hello, World", "World")') is True
    assert evaluate('Text.StartsWith(null, "x")') is None


def test_text_position_of_returns_minus_one_when_not_found():
    # Trap: -1, not null and not an error.
    assert evaluate('Text.PositionOf("abc", "z")') == -1


def test_text_position_of_first_and_last_occurrence():
    src_first = 'Text.PositionOf("Hello, World! Hello, World!", "World")'
    assert evaluate(src_first) == 7
    src_last = 'Text.PositionOf("Hello, World! Hello, World!", "World", 1)'
    assert evaluate(src_last) == 21


def test_text_position_of_all_occurrences():
    src = 'Text.PositionOf("aXaXa", "a", 2)'
    assert evaluate(src) == [0, 2, 4]


def test_text_position_of_bad_occurrence_is_unsupported():
    with pytest.raises(UnsupportedError, match="occurrence"):
        evaluate('Text.PositionOf("abc", "b", 5)')


def test_text_position_of_any():
    assert evaluate('Text.PositionOfAny("Hello, World!", {"H", "W"})') == 0
    src_all = 'Text.PositionOfAny("Hello, World!", {"H", "W"}, 2)'
    assert evaluate(src_all) == [0, 7]
    assert evaluate('Text.PositionOfAny("abc", {"z"})') == -1


def test_text_insert():
    assert evaluate('Text.Insert("ABD", 2, "C")') == "ABCD"


def test_text_insert_out_of_range_is_eval_error():
    with pytest.raises(EvalError, match="offset out of range"):
        evaluate('Text.Insert("AB", 50, "C")')


def test_text_proper_matches_ms_docs_example():
    src = 'Text.Proper("the QUICK BrOWn fOx jUmPs oVER tHe LAzy DoG")'
    assert evaluate(src) == "The Quick Brown Fox Jumps Over The Lazy Dog"


def test_text_clean_removes_control_characters():
    # Text.Clean("ABC#(lf)D") -> "ABCD" per MS docs; built with a real
    # embedded control character since this evaluator's string literal
    # lexer does not implement the #(lf)-style escape notation.
    src = 'Text.Clean("ABC' + "\n" + 'D")'
    assert evaluate(src) == "ABCD"


def test_text_trim_start_and_end():
    assert evaluate('Text.TrimStart("  hi  ")') == "hi  "
    assert evaluate('Text.TrimEnd("  hi  ")') == "  hi"


def test_text_to_list():
    assert evaluate('Text.ToList("Hi")') == ["H", "i"]


def test_text_at_in_range_and_out_of_range():
    assert evaluate('Text.At("Hello, World", 4)') == "o"
    # Trap: nullable return type -> out-of-range is null, not an error.
    assert evaluate('Text.At("Hi", 99)') is None


def test_text_split_any():
    src = 'Text.SplitAny("Name|Customer ID|Purchase|Month-Day-Year", "|-")'
    assert evaluate(src) == ["Name", "Customer ID", "Purchase", "Month", "Day", "Year"]


def test_text_new_guid_shape_only():
    # Non-deterministic - assert on shape, never on the exact value.
    guid = evaluate("Text.NewGuid()")
    assert re.match(
        r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$", guid
    )
    assert evaluate("Text.NewGuid()") != evaluate("Text.NewGuid()")


def test_text_repeat_huge_count_fails_fast_not_hang():
    with pytest.raises(EvalError, match="max_steps"):
        evaluate('Text.Repeat("x", 10000000000)', max_steps=1000)


# --------------------------------------------------------------------------
# List.*
# --------------------------------------------------------------------------


def test_list_combine_flattens_and_keeps_duplicates():
    assert evaluate("List.Combine({{1,2},{3,4}})") == [1, 2, 3, 4]
    assert evaluate("List.Combine({{1,1},{1}})") == [1, 1, 1]


def test_list_remove_nulls():
    assert evaluate("List.RemoveNulls({1,null,2,null,3})") == [1, 2, 3]


def test_list_remove_items_removes_every_occurrence():
    # Trap (verified against MS docs): removes ALL occurrences of a value
    # found in list2, not a one-per-match multiset removal.
    src = "List.RemoveItems({1,2,3,4,2,5,5},{2,4,6})"
    assert evaluate(src) == [1, 3, 5, 5]


def test_list_zip_pads_shorter_lists_with_null():
    assert evaluate("List.Zip({{1,2},{3,4}})") == [[1, 3], [2, 4]]
    assert evaluate("List.Zip({{1,2},{3}})") == [[1, 3], [2, None]]


def test_list_positions():
    assert evaluate("List.Positions({1,2,3,4,null,5})") == [0, 1, 2, 3, 4, 5]


def test_list_accumulate():
    assert evaluate("List.Accumulate({1,2,3,4,5}, 0, (s,n) => s+n)") == 15
    src = 'List.Accumulate({"a","b","c"}, "", (acc,x) => acc & x)'
    assert evaluate(src) == "abc"


def test_list_numbers():
    assert evaluate("List.Numbers(1,10)") == list(range(1, 11))
    assert evaluate("List.Numbers(1,5,2)") == [1, 3, 5, 7, 9]


def test_list_skip_default_is_one_not_zero():
    # Trap (verified against real docs): the omitted-argument default is
    # "skip the first element" (count = 1), not "skip 0".
    assert evaluate("List.Skip({1,2,3,4,5})") == [2, 3, 4, 5]


def test_list_skip_count_and_condition_forms():
    assert evaluate("List.Skip({1,2,3,4,5}, 3)") == [4, 5]
    assert evaluate("List.Skip({5,4,2,6,1}, each _ > 3)") == [2, 6, 1]


def test_list_first_n_count_and_condition_forms():
    assert evaluate("List.FirstN({1,2,3,4,5}, 3)") == [1, 2, 3]
    src = "List.FirstN({3,4,5,-1,7,8,2}, each _ > 0)"
    assert evaluate(src) == [3, 4, 5]


def test_list_last_n_requires_the_argument():
    # Trap (verified against docs' own caveat): although documented as
    # optional, omitting/nulling countOrCondition is an error.
    with pytest.raises(EvalError, match="required"):
        evaluate("List.LastN({1,2,3})")


def test_list_last_n_count_and_condition_forms():
    assert evaluate("List.LastN({3,4,5,-1,7,8,2}, 1)") == [2]
    src = "List.LastN({3,4,5,-1,7,8,2}, each _ > 0)"
    assert evaluate(src) == [7, 8, 2]


def test_list_contains_any_and_all():
    assert evaluate("List.ContainsAny({1,2,3,4,5}, {3,9})") is True
    assert evaluate("List.ContainsAny({1,2,3,4,5}, {6,7})") is False
    assert evaluate("List.ContainsAll({1,2,3,4,5}, {3,4})") is True
    assert evaluate("List.ContainsAll({1,2,3,4,5}, {5,6})") is False


def test_list_difference():
    assert evaluate("List.Difference({1,2,3,4,5}, {4,5,3})") == [1, 2]
    assert evaluate("List.Difference({1,2}, {1,2,3})") == []


def test_list_union_dedupes_across_all_input_lists():
    # Trap (verified via search cross-check of the real function): the
    # union is DEDUPED, unlike List.Combine which keeps duplicates.
    assert evaluate("List.Union({{1,1,2},{2,3}})") == [1, 2, 3]


def test_list_intersect():
    assert evaluate("List.Intersect({{1,2,3},{2,3,4},{3,4,5}})") == [3]


def test_list_repeat_repeats_the_whole_list():
    assert evaluate("List.Repeat({1,2}, 3)") == [1, 2, 1, 2, 1, 2]


def test_list_split_into_pages():
    assert evaluate("List.Split({1,2,3,4,5}, 2)") == [[1, 2], [3, 4], [5]]


def test_list_median_even_and_odd_counts():
    assert evaluate("List.Median({1,2,3,4,5})") == 3
    assert evaluate("List.Median({1,2,3,4})") == 2.5


def test_list_median_empty_is_null():
    assert evaluate("List.Median({})") is None


def test_list_mode_ties_go_to_the_last_value():
    # Trap (verified against MS docs example): {"A",1,2,3,3,4,5,5} -> 5,
    # NOT 3 - both 3 and 5 appear twice, but 5's occurrences finish later.
    assert evaluate('List.Mode({"A",1,2,3,3,4,5,5})') == 5


def test_list_mode_empty_is_eval_error():
    with pytest.raises(EvalError, match="must not be empty"):
        evaluate("List.Mode({})")


def test_list_standard_deviation_matches_ms_docs_example():
    result = evaluate("List.StandardDeviation({1,2,3,4,5})")
    assert result == pytest.approx(1.5811388300841898)


def test_list_standard_deviation_single_item_is_eval_error():
    with pytest.raises(EvalError, match="at least two"):
        evaluate("List.StandardDeviation({1})")


def test_list_percentile_single_and_list_form():
    assert evaluate("List.Percentile({5,3,1,7,9}, 0.25)") == 3
    assert evaluate("List.Percentile({5,3,1,7,9}, {0.25,0.5,0.75})") == [3, 5, 7]


def test_list_all_true_and_any_true():
    assert evaluate("List.AllTrue({true, true, 2 > 0})") is True
    assert evaluate("List.AllTrue({true, false, 2 < 0})") is False
    assert evaluate("List.AnyTrue({false, false, true})") is True
    assert evaluate("List.AnyTrue({false, false})") is False


def test_list_is_empty():
    assert evaluate("List.IsEmpty({})") is True
    assert evaluate("List.IsEmpty({1})") is False


def test_list_non_null_count():
    assert evaluate("List.NonNullCount({1,null,2,null,3})") == 3


def test_list_buffer_is_identity():
    assert evaluate("List.Buffer({1,2,3})") == [1, 2, 3]


def test_list_position_of():
    assert evaluate("List.PositionOf({1,2,3,2}, 2)") == 1
    assert evaluate("List.PositionOf({1,2,3,2}, 2, 1)") == 3
    assert evaluate("List.PositionOf({1,2,3}, 99)") == -1


def test_list_insert_range():
    assert evaluate("List.InsertRange({1,2,5}, 2, {3,4})") == [1, 2, 3, 4, 5]


def test_list_replace_value():
    src = "List.ReplaceValue({1,2,3}, 2, 99, (v,o,n) => if v = o then n else v)"
    assert evaluate(src) == [1, 99, 3]


def test_list_transform_signature_unchanged_single_arg_only():
    # Confirms real List.Transform is single-arg only (list, transform) -
    # the transform callback does NOT receive an index in real Power
    # Query, so the pre-existing implementation is left untouched. This
    # pins that a 3-argument call is rejected the same way it always was.
    with pytest.raises(UnsupportedError):
        evaluate("List.Transform({1,2}, each _ + 1, 3)")


# --------------------------------------------------------------------------
# List.Generate / List.Accumulate - function-value invocation + step budget
# --------------------------------------------------------------------------


def test_list_generate_basic():
    src = "List.Generate(() => 10, each _ > 0, each _ - 1)"
    assert evaluate(src) == list(range(10, 0, -1))


def test_list_generate_with_selector():
    # The optional 4th argument transforms each accepted value before it
    # is appended to the result list.
    src = """
        List.Generate(
            () => 1,
            each _ < 5,
            each _ + 1,
            each _ * 10
        )
    """
    assert evaluate(src) == [10, 20, 30, 40]


def test_list_generate_nonterminating_lambda_condition_hits_step_budget():
    # The trap this task calls out explicitly: a non-terminating
    # List.Generate must hit the step budget and RAISE, not hang.
    with pytest.raises(EvalError, match="max_steps"):
        evaluate("List.Generate(() => 0, each true, each _ + 1)", max_steps=500)


def test_list_generate_nonterminating_raw_builtin_condition_hits_step_budget():
    # Defense-in-depth case: condition/next passed as bare builtin
    # identifiers (not M lambdas) don't tick ctx.budget on their own via
    # ctx.invoke - the explicit tick in the List.Generate loop body must
    # still bound this, or it hangs forever (Number.IsEven(0) is always
    # true, Number.Abs(0) is always 0).
    with pytest.raises(EvalError, match="max_steps"):
        evaluate("List.Generate(() => 0, Number.IsEven, Number.Abs)", max_steps=500)


def test_list_numbers_huge_count_fails_fast_not_hang():
    with pytest.raises(EvalError, match="max_steps"):
        evaluate("List.Numbers(1, 10000000000)", max_steps=1000)


# --------------------------------------------------------------------------
# Number.*
# --------------------------------------------------------------------------


def test_number_integer_divide_truncates_toward_zero():
    assert evaluate("Number.IntegerDivide(6,4)") == 1
    assert evaluate("Number.IntegerDivide(8.3,3)") == 2
    # Trap: truncated toward zero, NOT floored - matches real PQ.
    assert evaluate("Number.IntegerDivide(-7,3)") == -2


def test_number_integer_divide_by_zero_is_eval_error():
    with pytest.raises(EvalError, match="division by zero"):
        evaluate("Number.IntegerDivide(1,0)")


def test_number_integer_divide_null_propagates():
    assert evaluate("Number.IntegerDivide(null, 2)") is None


def test_number_mod_is_truncated_not_floored():
    # Trap (verified: this is the single biggest Number.Mod gotcha).
    # Number.Mod(-7, 3) is -1 in real Power Query (truncated/C-style
    # modulo), NOT the 2 that Python's -7 % 3 gives (floored modulo).
    assert evaluate("Number.Mod(5,3)") == 2
    assert evaluate("Number.Mod(-7,3)") == -1


def test_number_mod_by_zero_is_eval_error():
    with pytest.raises(EvalError, match="division by zero"):
        evaluate("Number.Mod(1,0)")


def test_number_mod_null_propagates():
    assert evaluate("Number.Mod(null, 3)") is None


def test_number_power():
    assert evaluate("Number.Power(2,10)") == 1024


def test_number_sqrt_negative_is_nan_not_error():
    # Trap (verified against docs): a negative input returns Number.NaN,
    # it does NOT raise.
    result = evaluate("Number.Sqrt(-4)")
    assert isinstance(result, float) and math.isnan(result)
    assert evaluate("Number.Sqrt(625)") == 25


def test_number_sqrt_null_propagates():
    assert evaluate("Number.Sqrt(null)") is None


def test_number_exp_and_ln():
    assert evaluate("Number.Exp(0)") == 1
    assert evaluate("Number.Ln(1)") == 0


def test_number_log_default_base_is_e():
    result = evaluate("Number.Log(2)")
    assert result == pytest.approx(math.log(2))


def test_number_log_explicit_base():
    result = evaluate("Number.Log(2, 10)")
    assert result == pytest.approx(0.3010299956639812)


def test_number_log10():
    assert evaluate("Number.Log10(100)") == pytest.approx(2.0)


def test_number_sign():
    assert evaluate("Number.Sign(-5)") == -1
    assert evaluate("Number.Sign(0)") == 0
    assert evaluate("Number.Sign(5)") == 1


def test_number_round_up_and_down_match_ms_docs_examples():
    assert evaluate("Number.RoundUp(1.234)") == 2
    assert evaluate("Number.RoundUp(1.999)") == 2
    assert evaluate("Number.RoundUp(1.234,2)") == pytest.approx(1.24)
    assert evaluate("Number.RoundDown(1.999)") == 1


def test_number_round_away_from_zero_is_not_half_rounding():
    # Trap (verified): this rounds ANY fraction away from zero, not just
    # ties - Number.RoundAwayFromZero(1.2) is 2, not 1.
    assert evaluate("Number.RoundAwayFromZero(-1.2)") == -2
    assert evaluate("Number.RoundAwayFromZero(1.2)") == 2
    assert evaluate("Number.RoundAwayFromZero(-1.234,2)") == pytest.approx(-1.24)


def test_number_round_toward_zero_truncates():
    # Trap (verified): Number.RoundTowardZero(-1.2) is -1, not -2.
    assert evaluate("Number.RoundTowardZero(-1.2)") == -1
    assert evaluate("Number.RoundTowardZero(1.2)") == 1
    assert evaluate("Number.RoundTowardZero(-1.234,2)") == pytest.approx(-1.23)


def test_number_round_variants_null_propagate():
    assert evaluate("Number.RoundUp(null)") is None
    assert evaluate("Number.RoundDown(null)") is None
    assert evaluate("Number.RoundAwayFromZero(null)") is None
    assert evaluate("Number.RoundTowardZero(null)") is None


def test_number_to_text_default_and_fixed_format():
    assert evaluate("Number.ToText(4)") == "4"
    assert evaluate('Number.ToText(1.5, "F2")') == "1.50"


def test_number_to_text_unsupported_format_raises():
    with pytest.raises(UnsupportedError, match="format"):
        evaluate('Number.ToText(4, "e")')


def test_number_is_nan():
    assert evaluate("Number.IsNaN(#nan)") is True
    assert evaluate("Number.IsNaN(1)") is False


def test_number_is_even_and_odd():
    assert evaluate("Number.IsEven(4)") is True
    assert evaluate("Number.IsOdd(4)") is False
    assert evaluate("Number.IsEven(null)") is None


def test_number_bitwise_ops():
    assert evaluate("Number.BitwiseAnd(6,3)") == 2
    assert evaluate("Number.BitwiseOr(6,1)") == 7
    assert evaluate("Number.BitwiseXor(6,3)") == 5


def test_number_factorial():
    assert evaluate("Number.Factorial(10)") == 3628800
    assert evaluate("Number.Factorial(0)") == 1


def test_number_factorial_negative_is_eval_error():
    with pytest.raises(EvalError, match="not be negative"):
        evaluate("Number.Factorial(-1)")


def test_number_factorial_huge_input_fails_fast_not_hang():
    with pytest.raises(EvalError, match="max_steps"):
        evaluate("Number.Factorial(1000000000)", max_steps=1000)


def test_number_random_range_only_not_exact_value():
    # Non-deterministic - assert range, never exact value.
    for _ in range(5):
        assert 0 <= evaluate("Number.Random()") < 1


def test_number_random_between_range_only_not_exact_value():
    for _ in range(5):
        result = evaluate("Number.RandomBetween(5, 10)")
        assert 5 <= result <= 10


def test_number_random_between_bottom_after_top_is_eval_error():
    with pytest.raises(EvalError, match="must not exceed"):
        evaluate("Number.RandomBetween(10, 5)")


# --------------------------------------------------------------------------
# Number.Round - REPORT ONLY, do not change existing behaviour/tests.
# This pins (without modifying) what the EXISTING Number.Round already
# does, to document the cross-check this task's PRD asked for: is it real
# banker's rounding (round-half-to-even)? Answer: yes.
# --------------------------------------------------------------------------


def test_existing_number_round_is_real_bankers_rounding():
    # Ties round to the nearest EVEN number, matching Power Query's
    # documented default (RoundingMode.ToEven): 0.5->0, 1.5->2, 2.5->2.
    assert evaluate("Number.Round(0.5)") == 0
    assert evaluate("Number.Round(1.5)") == 2
    assert evaluate("Number.Round(2.5)") == 2
    assert evaluate("Number.Round(-1.5)") == -2
    # Non-tie cases from the MS docs' own worked examples.
    assert evaluate("Number.Round(1.234)") == 1
    assert evaluate("Number.Round(1.56)") == 2
    assert evaluate("Number.Round(1.2345, 2)") == pytest.approx(1.23)


# --------------------------------------------------------------------------
# Record.*
# --------------------------------------------------------------------------


def test_record_to_list():
    assert evaluate("Record.ToList([a=1,b=2,c=3])") == [1, 2, 3]


def test_record_from_list():
    src = 'Record.FromList({1,"Bob","123-4567"}, {"CustomerID","Name","Phone"})'
    assert evaluate(src) == {"CustomerID": 1, "Name": "Bob", "Phone": "123-4567"}


def test_record_from_list_mismatched_counts_is_eval_error():
    with pytest.raises(EvalError, match="does not match"):
        evaluate('Record.FromList({1,2}, {"a"})')


def test_record_combine_last_write_wins_on_collision():
    assert evaluate("Record.Combine({[a=1],[a=2]})") == {"a": 2}
    src = 'Record.Combine({[CustomerID=1,Name="Bob"],[Phone="123-4567"]})'
    assert evaluate(src) == {"CustomerID": 1, "Name": "Bob", "Phone": "123-4567"}


def test_record_select_fields():
    src = """
        Record.SelectFields(
            [OrderID=1,CustomerID=1,Item="Fishing rod",Price=100.0],
            {"Item","Price"}
        )
    """
    assert evaluate(src) == {"Item": "Fishing rod", "Price": 100.0}


def test_record_select_fields_missing_field_default_errors():
    with pytest.raises(EvalError, match="no such field"):
        evaluate('Record.SelectFields([a=1], {"b"})')


def test_record_select_fields_missing_field_use_null():
    src = 'Record.SelectFields([a=1], {"a","b"}, 2)'
    assert evaluate(src) == {"a": 1, "b": None}


def test_record_select_fields_missing_field_ignore():
    src = 'Record.SelectFields([a=1], {"a","b"}, 1)'
    assert evaluate(src) == {"a": 1}


def test_record_rename_fields_single_pair():
    src = """
        Record.RenameFields(
            [CustomerID=1,OrderID=1,Item="Fishing rod",UnitPrice=100.0],
            {"UnitPrice","Price"}
        )
    """
    assert evaluate(src) == {
        "CustomerID": 1,
        "OrderID": 1,
        "Item": "Fishing rod",
        "Price": 100.0,
    }


def test_record_rename_fields_preserves_original_position():
    # Trap (verified against MS docs example): a renamed field stays at
    # its ORIGINAL position, it does not move to the end of the record.
    src = """
        Record.RenameFields(
            [OrderNum=1,CustomerID=1,Item="Fishing rod",UnitPrice=100.0],
            {{"UnitPrice","Price"},{"OrderNum","OrderID"}}
        )
    """
    result = evaluate(src)
    assert list(result.keys()) == ["OrderID", "CustomerID", "Item", "Price"]
    assert result == {
        "OrderID": 1,
        "CustomerID": 1,
        "Item": "Fishing rod",
        "Price": 100.0,
    }


def test_record_transform_fields_single_and_multi():
    src1 = 'Record.TransformFields([Price="100.0"], {"Price", Number.From})'
    assert evaluate(src1) == {"Price": 100.0}
    src2 = """
        Record.TransformFields(
            [OrderID="1", Price="100.0"],
            {{"OrderID", Number.From}, {"Price", Number.From}}
        )
    """
    assert evaluate(src2) == {"OrderID": 1, "Price": 100.0}


def test_record_to_table():
    src = "Record.ToTable([OrderID=1,CustomerID=1])"
    assert evaluate(src) == [
        {"Name": "OrderID", "Value": 1},
        {"Name": "CustomerID", "Value": 1},
    ]


def test_record_field_or_default():
    assert evaluate('Record.FieldOrDefault([CustomerID=1,Name="Bob"], "Phone")') is None
    src = 'Record.FieldOrDefault([CustomerID=1,Name="Bob"], "Phone", "123-4567")'
    assert evaluate(src) == "123-4567"


def test_record_field_count():
    assert evaluate("Record.FieldCount([a=1,b=2,c=3])") == 3


def test_record_reorder_fields_basic():
    src = """
        Record.ReorderFields(
            [CustomerID=1,OrderID=1,Item="Fishing rod",Price=100.0],
            {"OrderID","CustomerID"}
        )
    """
    result = evaluate(src)
    assert list(result.keys()) == ["OrderID", "CustomerID", "Item", "Price"]


def test_record_reorder_fields_with_use_null_adds_a_new_field_in_place():
    # Trap (verified against the MS docs' second worked example, the
    # trickiest one in the whole Record family): fields NOT named in
    # fieldOrder keep their ORIGINAL numeric position; a brand-new field
    # (via MissingField.UseNull) is slotted in among the others by
    # fieldOrder's own order, not appended at the very end.
    src = """
        let
            Source = [
                CustomerID = 3, FirstName = "Paul",
                Phone = "543-7890", Purchase = "Fishing Rod"
            ]
        in
            Record.ReorderFields(Source, {"Purchase", "LastName", "FirstName"}, 2)
    """
    result = evaluate(src)
    assert list(result.keys()) == [
        "CustomerID",
        "Purchase",
        "Phone",
        "LastName",
        "FirstName",
    ]
    assert result == {
        "CustomerID": 3,
        "Purchase": "Fishing Rod",
        "Phone": "543-7890",
        "LastName": None,
        "FirstName": "Paul",
    }


def test_record_reorder_fields_missing_field_default_errors():
    with pytest.raises(EvalError, match="no such field"):
        evaluate('Record.ReorderFields([a=1], {"b"})')
