"""Tests for ``equationCriteria``/``comparer`` support across ``Text.*`` and
``List.*``. Every scenario here was verified against a Microsoft Learn
worked example before being pinned - see the comments in ``_text.py``/
``_list.py`` for the source URLs. Deliberately-kept refusals (unsupported
occurrence values, ``Comparer.FromCulture``, custom comparers on the list
functions real Power Query restricts) get their own tests too, so a future
change that accidentally "fixes" one of them is caught.
"""

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Comparer.* called directly
# --------------------------------------------------------------------------


def test_comparer_ordinal_returns_signed_ordering():
    assert evaluate('Comparer.Ordinal("a", "a")') == 0
    assert evaluate('Comparer.Ordinal("a", "b")') == -1
    assert evaluate('Comparer.Ordinal("b", "a")') == 1


def test_comparer_ordinal_is_case_sensitive():
    # Verified against MS docs: Comparer.Equals(Comparer.Ordinal,
    # "encyclopædia", "encyclopaedia") -> false. Case matters too.
    assert evaluate('Comparer.Ordinal("Abc", "abc")') != 0


def test_comparer_ordinal_ignore_case_verified_example():
    # Verified: Comparer.OrdinalIgnoreCase("Abc", "abc") -> 0.
    assert evaluate('Comparer.OrdinalIgnoreCase("Abc", "abc")') == 0
    assert evaluate('Comparer.OrdinalIgnoreCase("abc", "abd")') == -1


def test_comparer_ordinal_compares_non_text_values_too():
    """This asserted the opposite, on a premise that has since been settled.

    The old reasoning was sound at the time: an "Ordinal" comparison is a
    codepoint rule, every verified example compared text, and inventing a
    non-text rule would have been approximation. But the evidence existed and
    had not been looked for. Table.RemoveMatchingRows Example 2 passes
    Comparer.OrdinalIgnoreCase as the equation criteria for a table whose
    columns are `OrderID = number`, `Product = text`, `Quantity = number`,
    and the page's printed output has the matching row REMOVED - which is
    only reachable if the comparer accepts the two numbers. The signature
    says the same thing plainly: `(x as any, y as any)`.

    "Ordinal" governs how TEXT is compared. For everything else there is no
    codepoint order to take, so these defer to M's own default ordering -
    the rule Value.Compare already implements here.
    """
    assert evaluate("Comparer.Ordinal(1, 2)") == -1
    assert evaluate("Comparer.OrdinalIgnoreCase(2, 2)") == 0
    assert evaluate("Comparer.Ordinal(#date(2021, 1, 1), #date(2020, 1, 1))") == 1


def test_comparer_ordinal_still_refuses_to_compare_across_types():
    # M has no cross-type ordering. Inventing one here would silently decide
    # a comparison the language itself declines to make.
    with pytest.raises((EvalError, UnsupportedError)):
        evaluate('Comparer.Ordinal("a", 2)')


def test_comparer_from_culture_is_refused_when_called():
    with pytest.raises(UnsupportedError, match="Comparer.FromCulture"):
        evaluate('Comparer.FromCulture("en-US")')


def test_comparer_from_culture_is_refused_bare_as_an_argument():
    # The bare, uninvoked identifier reaches Text.Contains's comparer
    # resolver directly (M evaluates call arguments eagerly, so a *call*
    # like Comparer.FromCulture("en-US") never survives to be passed
    # anywhere - only the bare reference does).
    with pytest.raises(UnsupportedError, match="Comparer.FromCulture"):
        evaluate('Text.Contains("a", "a", Comparer.FromCulture)')


# --------------------------------------------------------------------------
# Text.Contains
# --------------------------------------------------------------------------


def test_text_contains_case_insensitive_verified_example():
    # Verified: Text.Contains("Hello World", "hello", Comparer.OrdinalIgnoreCase)
    # -> true.
    assert (
        evaluate('Text.Contains("Hello World", "hello", Comparer.OrdinalIgnoreCase)')
        is True
    )
    assert evaluate('Text.Contains("Hello World", "hello")') is False


def test_text_contains_explicit_ordinal_is_exact_match():
    assert evaluate('Text.Contains("Hello World", "hello", Comparer.Ordinal)') is False
    assert evaluate('Text.Contains("Hello World", "Hello", Comparer.Ordinal)') is True


def test_text_contains_null_text_propagates():
    # Verified: "If the first argument is null, this function returns
    # null" - the prior 2-arg-only implementation never reached this path.
    assert evaluate('Text.Contains(null, "a")') is None


def test_text_contains_rejects_an_unrecognised_comparer():
    with pytest.raises(UnsupportedError, match="Text.Contains"):
        evaluate('Text.Contains("a", "a", 5)')


# --------------------------------------------------------------------------
# Text.StartsWith / Text.EndsWith
# --------------------------------------------------------------------------


def test_text_starts_with_case_insensitive_verified_example():
    # Verified: Text.StartsWith("Hello, World", "hello",
    # Comparer.OrdinalIgnoreCase) -> true.
    assert (
        evaluate('Text.StartsWith("Hello, World", "hello", Comparer.OrdinalIgnoreCase)')
        is True
    )
    assert evaluate('Text.StartsWith("Hello, World", "hello")') is False


def test_text_ends_with_case_insensitive():
    assert (
        evaluate('Text.EndsWith("Hello, World", "WORLD", Comparer.OrdinalIgnoreCase)')
        is True
    )
    assert evaluate('Text.EndsWith("Hello, World", "WORLD")') is False


def test_text_starts_with_and_ends_with_null_still_propagate():
    assert evaluate('Text.StartsWith(null, "a", Comparer.OrdinalIgnoreCase)') is None
    assert evaluate('Text.EndsWith(null, "a", Comparer.OrdinalIgnoreCase)') is None


# --------------------------------------------------------------------------
# Text.PositionOf
# --------------------------------------------------------------------------


def test_text_position_of_case_insensitive_first_and_all():
    text = "Hello, World! hello, world!"
    assert (
        evaluate(
            f'Text.PositionOf("{text}", "hello", null, Comparer.OrdinalIgnoreCase)'
        )
        == 0
    )
    src_all = (
        f'Text.PositionOf("{text}", "hello", Occurrence.All, '
        "Comparer.OrdinalIgnoreCase)"
    )
    assert evaluate(src_all) == [0, 14]


def test_text_position_of_case_insensitive_last_allows_overlap():
    # str.rfind allows overlapping matches ("aaaaa".rfind("aa") -> 3, not
    # 2, since Occurrence.All's non-overlapping scan would stop at 2). The
    # case-insensitive path has to match that, not the simpler
    # non-overlapping scan - verified case-sensitively first as the
    # baseline, then case-insensitively against mixed-case input.
    assert evaluate('Text.PositionOf("aaaaa", "aa", Occurrence.Last)') == 3
    assert (
        evaluate(
            'Text.PositionOf("AaAaA", "aa", Occurrence.Last, '
            "Comparer.OrdinalIgnoreCase)"
        )
        == 3
    )


def test_text_position_of_not_found_is_minus_one_with_comparer():
    assert (
        evaluate('Text.PositionOf("abc", "z", null, Comparer.OrdinalIgnoreCase)') == -1
    )
    assert (
        evaluate(
            'Text.PositionOf("abc", "z", Occurrence.Last, Comparer.OrdinalIgnoreCase)'
        )
        == -1
    )


def test_text_position_of_occurrence_still_refused_when_invalid():
    with pytest.raises(UnsupportedError, match="occurrence"):
        evaluate('Text.PositionOf("abc", "a", 3)')


# --------------------------------------------------------------------------
# List.ContainsAny / List.ContainsAll - real PQ allows a CUSTOM comparer here
# --------------------------------------------------------------------------


def test_list_contains_any_builtin_comparer_verified_example():
    src = (
        'List.ContainsAny({"dog", "cat", "racoon", "horse", "rabbit"}, '
        '{"Horse", "OWL"}, Comparer.OrdinalIgnoreCase)'
    )
    assert evaluate(src) is True


def test_list_contains_all_builtin_comparer_verified_example():
    src = (
        'List.ContainsAll({"dog", "cat", "racoon", "horse", "rabbit"}, '
        '{"DOG", "Horse"}, Comparer.OrdinalIgnoreCase)'
    )
    assert evaluate(src) is True


def test_list_contains_any_accepts_a_custom_two_argument_comparer():
    # ContainsAny is one of the five functions real PQ lets use a custom
    # comparer on - a plain numeric -1/0/1 comparer (not the logical-return
    # shape List.PositionOf's own example uses), to pin the other return
    # shape _equation_match_result honours.
    src = (
        "List.ContainsAny({1, 2, 3}, {10}, "
        "(x, y) => if x = y then 0 else if x < y then -1 else 1)"
    )
    assert evaluate(src) is False
    src_hit = (
        "List.ContainsAny({1, 2, 3}, {2}, "
        "(x, y) => if x = y then 0 else if x < y then -1 else 1)"
    )
    assert evaluate(src_hit) is True


def test_list_contains_any_key_selector_form():
    # The selector applies uniformly to items on both sides (list AND
    # values) - both are the same shape (2-item sublists) here, matching
    # the base "list of comparable values" contract these functions
    # already have without a selector.
    src_hit = 'List.ContainsAny({{"a", 1}, {"b", 2}}, {{"z", 9}, {"b", 99}}, each _{0})'
    src_miss = (
        'List.ContainsAny({{"a", 1}, {"b", 2}}, {{"z", 9}, {"y", 99}}, each _{0})'
    )
    assert evaluate(src_hit) is True
    assert evaluate(src_miss) is False


# --------------------------------------------------------------------------
# List.Difference / List.Intersect / List.Union / List.Mode - real PQ
# restricts these to a KEY SELECTOR and/or a BUILT-IN comparer; a custom
# comparer "results in an error" per Microsoft's own docs.
# --------------------------------------------------------------------------


def test_list_difference_verified_examples():
    assert evaluate("List.Difference({1, 2, 3, 4, 5}, {4, 5, 3})") == [1, 2]
    assert evaluate("List.Difference({1, 2}, {1, 2, 3})") == []


def test_list_difference_with_builtin_comparer():
    src = 'List.Difference({"apple", "Banana"}, {"APPLE"}, Comparer.OrdinalIgnoreCase)'
    assert evaluate(src) == ["Banana"]


def test_list_difference_rejects_a_custom_comparer():
    with pytest.raises(UnsupportedError, match="custom comparer"):
        evaluate("List.Difference({1, 2}, {2}, (x, y) => if x = y then 0 else 1)")


def test_list_intersect_verified_example():
    src = "List.Intersect({{1, 2, 3, 4, 5}, {2, 3, 4, 5, 6}, {3, 4, 5, 6, 7}})"
    assert evaluate(src) == [3, 4, 5]


def test_list_intersect_key_selector_form():
    src = 'List.Intersect({{"a", "B"}, {"A", "b"}}, Comparer.OrdinalIgnoreCase)'
    assert evaluate(src) == ["a", "B"]


def test_list_union_verified_example():
    src = "List.Union({{1, 2, 3, 4, 5}, {2, 3, 4, 5, 6}, {3, 4, 5, 6, 7}})"
    assert evaluate(src) == [1, 2, 3, 4, 5, 6, 7]


def test_list_union_with_builtin_comparer_dedupes_case_insensitively():
    assert evaluate('List.Union({{"a"}, {"A"}}, Comparer.OrdinalIgnoreCase)') == ["a"]


def test_list_union_rejects_a_custom_comparer():
    with pytest.raises(UnsupportedError, match="custom comparer"):
        evaluate("List.Union({{1, 2}}, (x, y) => if x = y then 0 else 1)")


def test_list_mode_verified_examples():
    assert evaluate('List.Mode({"A", 1, 2, 3, 3, 4, 5})') == 3
    assert evaluate('List.Mode({"A", 1, 2, 3, 3, 4, 5, 5})') == 5


def test_list_mode_tie_rule_is_pinned_where_the_docs_are_silent():
    """The published example cannot settle which "later" wins on a tie.

    {"A",1,2,3,3,4,5,5} -> 5 holds under both "latest first occurrence" and
    "latest last occurrence". {5,3,3,5} separates them: 3 under the first,
    5 under the second. This pins the rule the package actually implements
    so a refactor cannot flip it unnoticed, and so nobody reads the passing
    docs example as proof the tie rule was verified. It was not.
    """
    assert evaluate("List.Mode({5,3,3,5})") == 3
    assert evaluate("List.Mode({3,5,5,3})") == 5


def test_list_mode_with_builtin_comparer():
    assert (
        evaluate('List.Mode({"apple", "Apple", "banana"}, Comparer.OrdinalIgnoreCase)')
        == "apple"
    )


def test_list_mode_rejects_a_custom_comparer():
    with pytest.raises(UnsupportedError, match="custom comparer"):
        evaluate("List.Mode({1, 2, 3}, (x, y) => if x = y then 0 else 1)")


def test_list_mode_still_requires_a_non_empty_list():
    with pytest.raises(EvalError, match="must not be empty"):
        evaluate("List.Mode({})")


# --------------------------------------------------------------------------
# List.PositionOf / List.PositionOfAny - real PQ allows a CUSTOM comparer.
# List.PositionOfAny did not exist in this evaluator at all before this
# change (no BUILTINS entry, no raise site) - it is a new builtin, not a
# refusal being lifted.
# --------------------------------------------------------------------------


def test_list_position_of_verified_examples():
    assert evaluate("List.PositionOf({1, 2, 3}, 3)") == 2
    dates = (
        "{#date(2021,5,10), #date(2022,6,28), #date(2023,7,15), "
        "#date(2022,12,31), #date(2022,4,8), #date(2024,3,20)}"
    )
    years = f"List.Transform({dates}, each Date.Year(_))"
    assert evaluate(f"List.PositionOf({years}, 2022, Occurrence.All)") == [1, 3, 4]


def test_list_position_of_builtin_comparer_verified_example():
    src = (
        'List.PositionOf({"dog", "cat", "DOG", "pony", "bat", "rabbit", "dOG"}, '
        '"dog", Occurrence.Last, Comparer.OrdinalIgnoreCase)'
    )
    assert evaluate(src) == 6


def test_list_position_of_custom_comparer_verified_example():
    # Distance-within-2, returning a plain logical rather than -1/0/1 -
    # pins _equation_match_result's boolean-return path.
    src = (
        "List.PositionOf({10, 15, 20, 25, 30}, 28, Occurrence.First, "
        "(x, y) => Number.Abs(x - y) <= 2)"
    )
    assert evaluate(src) == 4


def test_list_position_of_pair_form_key_selector_and_comparer():
    # `value` gets the same selector applied as list items do (both are
    # 2-item sublists here) - matching the base "same shape" contract
    # List.PositionOf already has between `list` and `value` without a
    # selector.
    src = (
        'List.PositionOf({{"USA", 1}, {"canada", 2}, {"Usa", 3}}, {"usa", 99}, null, '
        "{each _{0}, Comparer.OrdinalIgnoreCase})"
    )
    assert evaluate(src) == 0


def test_list_position_of_not_found():
    assert evaluate("List.PositionOf({1, 2, 3}, 9)") == -1


def test_list_position_of_any_verified_examples():
    assert evaluate("List.PositionOfAny({1, 2, 3}, {2, 3})") == 1
    dates = (
        "{#date(2021,5,10), #date(2022,6,28), #date(2023,7,15), "
        "#date(2025,12,31), #date(2022,4,8), #date(2024,3,20)}"
    )
    years = f"List.Transform({dates}, each Date.Year(_))"
    src = f"List.PositionOfAny({years}, {{2022, 2023}}, Occurrence.All)"
    assert evaluate(src) == [1, 2, 4]


def test_list_position_of_any_builtin_comparer_verified_example():
    src = (
        'List.PositionOfAny({"dog", "cat", "DOG", "pony", "bat", "rabbit", "dOG"}, '
        '{"dog", "cat"}, Occurrence.Last, Comparer.OrdinalIgnoreCase)'
    )
    assert evaluate(src) == 6


def test_list_position_of_any_custom_comparer_verified_example():
    src = (
        "List.PositionOfAny({10, 15, 20, 25, 30}, {17, 28}, Occurrence.All, "
        "(x, y) => Number.Abs(x - y) <= 2)"
    )
    assert evaluate(src) == [1, 4]


def test_list_position_of_any_not_found():
    assert evaluate("List.PositionOfAny({1, 2, 3}, {9, 10})") == -1


def test_list_position_of_any_occurrence_still_refused_when_invalid():
    with pytest.raises(UnsupportedError, match="occurrence"):
        evaluate("List.PositionOfAny({1, 2, 3}, {2}, 3)")


def test_list_position_of_any_wrong_arity_is_unsupported():
    with pytest.raises(UnsupportedError, match="List.PositionOfAny"):
        evaluate("List.PositionOfAny({1, 2, 3})")


# --------------------------------------------------------------------------
# equationCriteria error paths shared by every list function above
# --------------------------------------------------------------------------


def test_equation_criteria_unknown_arity_is_refused_not_guessed():
    with pytest.raises(UnsupportedError, match="unknown arity"):
        evaluate("List.PositionOf({1, 2, 3}, 2, null, () => true)")
    with pytest.raises(UnsupportedError, match="unknown arity"):
        evaluate("List.PositionOf({1, 2, 3}, 2, null, (a, b, c) => true)")


def test_equation_criteria_list_form_must_have_two_items():
    with pytest.raises(UnsupportedError, match="exactly two items"):
        evaluate("List.PositionOf({1, 2, 3}, 2, null, {1, 2, 3})")


def test_equation_criteria_comparer_must_return_logical_or_number():
    with pytest.raises(EvalError, match="logical or a number"):
        evaluate('List.PositionOf({1, 2, 3}, 2, null, (x, y) => "nope")')
