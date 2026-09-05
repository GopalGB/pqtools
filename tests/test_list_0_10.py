"""Tests for the 0.10.0 ``List.*`` gap-fill: the 25 names Microsoft
documents at learn.microsoft.com/en-us/powerquery-m/list-* that pqtools did
not implement before this change. Every function gets at least one test,
including its deliberate refusals. Where a docs page leaves an edge case
open, the comment on the test says so explicitly and names the choice this
package pins - it does not claim the choice is "verified" M behaviour.
"""

import math
import statistics

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# List.Alternate
# --------------------------------------------------------------------------


def test_list_alternate_matches_all_four_docs_examples():
    items = "{1,2,3,4,5,6,7,8,9,10}"
    assert evaluate(f"List.Alternate({items}, 1)") == [2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert evaluate(f"List.Alternate({items}, 1, 1)") == [2, 4, 6, 8, 10]
    assert evaluate(f"List.Alternate({items}, 1, 1, 1)") == [1, 3, 5, 7, 9]
    assert evaluate(f"List.Alternate({items}, 1, 2, 1)") == [1, 3, 4, 6, 7, 9, 10]


def test_list_alternate_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate("List.Alternate({1,2,3}, -1)")


def test_list_alternate_zero_count_and_repeat_interval_hits_step_budget():
    # count=0 and repeatInterval=0 never advance the internal cursor - the
    # docs never address this input. Rather than hang, this package's
    # ctx.budget.tick() guard (the same protection List.Generate uses for a
    # non-terminating condition) turns it into the standard max_steps error.
    with pytest.raises(EvalError, match="max_steps"):
        evaluate("List.Alternate({1,2,3}, 0, 0)", max_steps=1000)


# --------------------------------------------------------------------------
# List.FindText
# --------------------------------------------------------------------------


def test_list_findtext_matches_docs_example():
    assert evaluate('List.FindText({"a", "b", "ab"}, "a")') == ["a", "ab"]


def test_list_findtext_is_case_sensitive_by_default():
    # Not proven by the docs' own (single-case) example; mirrors
    # Text.Contains' own default (case-sensitive unless a comparer says
    # otherwise) - a documented choice, not an independently verified fact.
    assert evaluate('List.FindText({"Apple", "apple"}, "apple")') == ["apple"]


def test_list_findtext_skips_non_text_items_rather_than_erroring():
    # A non-text item cannot "contain" text; the docs never show a mixed
    # list, so skipping (not raising) is this package's documented choice.
    assert evaluate('List.FindText({1, "cab", true}, "a")') == ["cab"]


# --------------------------------------------------------------------------
# List.IsDistinct
# --------------------------------------------------------------------------


def test_list_isdistinct_matches_docs_examples():
    assert evaluate("List.IsDistinct({1, 2, 3})") is True
    assert evaluate("List.IsDistinct({1, 2, 3, 3})") is False


def test_list_isdistinct_with_equation_criteria():
    assert (
        evaluate('List.IsDistinct({"a", "A"}, {each Text.Upper(_), Comparer.Ordinal})')
        is False
    )


# --------------------------------------------------------------------------
# List.MatchesAll / List.MatchesAny
# --------------------------------------------------------------------------


def test_list_matchesall_matches_docs_examples():
    assert evaluate("List.MatchesAll({11, 12, 13}, each _ > 10)") is True
    assert evaluate("List.MatchesAll({1, 2, 3}, each _ > 10)") is False


def test_list_matchesany_matches_docs_examples():
    assert evaluate("List.MatchesAny({9, 10, 11}, each _ > 10)") is True
    assert evaluate("List.MatchesAny({1, 2, 3}, each _ > 10)") is False


def test_list_matchesall_and_any_empty_list_is_a_documented_choice():
    # Neither docs page shows an empty list. True/False here follows the
    # conventional vacuous-truth reading (Python's own all([])/any([])),
    # pinned as a choice, not a verified MS fact.
    assert evaluate("List.MatchesAll({}, each _ > 10)") is True
    assert evaluate("List.MatchesAny({}, each _ > 10)") is False


# --------------------------------------------------------------------------
# List.Single / List.SingleOrDefault
# --------------------------------------------------------------------------


def test_list_single_matches_docs_examples():
    assert evaluate("List.Single({1})") == 1
    with pytest.raises(EvalError, match="too many elements"):
        evaluate("List.Single({1, 2, 3})")


def test_list_single_empty_is_eval_error():
    with pytest.raises(EvalError, match="empty"):
        evaluate("List.Single({})")


def test_list_singleordefault_matches_docs_examples():
    assert evaluate("List.SingleOrDefault({1})") == 1
    assert evaluate("List.SingleOrDefault({})") is None
    assert evaluate("List.SingleOrDefault({}, -1)") == -1


def test_list_singleordefault_too_many_is_eval_error():
    with pytest.raises(EvalError, match="too many elements"):
        evaluate("List.SingleOrDefault({1, 2, 3})")


# --------------------------------------------------------------------------
# List.RemoveFirstN / List.RemoveLastN
# --------------------------------------------------------------------------


def test_list_removefirstn_matches_docs_examples():
    assert evaluate("List.RemoveFirstN({1, 2, 3, 4, 5}, 3)") == [4, 5]
    assert evaluate("List.RemoveFirstN({5, 4, 2, 6, 1}, each _ > 3)") == [2, 6, 1]


def test_list_removefirstn_omitted_removes_exactly_one():
    assert evaluate("List.RemoveFirstN({1, 2, 3})") == [2, 3]
    assert evaluate("List.RemoveFirstN({})") == []


def test_list_removefirstn_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate("List.RemoveFirstN({1, 2, 3}, -1)")


def test_list_removelastn_matches_docs_examples():
    assert evaluate("List.RemoveLastN({1, 2, 3, 4, 5}, 3)") == [1, 2]
    assert evaluate("List.RemoveLastN({5, 4, 2, 6, 4}, each _ > 3)") == [5, 4, 2]


def test_list_removelastn_omitted_removes_exactly_one():
    # The docs' own words for the omitted/null case: "only one item is
    # removed".
    assert evaluate("List.RemoveLastN({1, 2, 3})") == [1, 2]
    assert evaluate("List.RemoveLastN({})") == []


def test_list_removelastn_count_exceeding_length_returns_empty():
    assert evaluate("List.RemoveLastN({1, 2}, 5)") == []


def test_list_removelastn_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate("List.RemoveLastN({1, 2, 3}, -1)")


# --------------------------------------------------------------------------
# List.RemoveMatchingItems / List.ReplaceMatchingItems
# --------------------------------------------------------------------------


def test_list_removematchingitems_matches_docs_example():
    assert evaluate("List.RemoveMatchingItems({1, 2, 3, 4, 5, 5}, {1, 5})") == [
        2,
        3,
        4,
    ]


def test_list_removematchingitems_with_equation_criteria():
    result = evaluate(
        'List.RemoveMatchingItems({"a", "A", "b"}, {"a"}, '
        "{each Text.Upper(_), Comparer.Ordinal})"
    )
    assert result == ["b"]


def test_list_replacematchingitems_matches_docs_example():
    result = evaluate("List.ReplaceMatchingItems({1, 2, 3, 4, 5}, {{5, -5}, {1, -1}})")
    assert result == [-1, 2, 3, 4, -5]


def test_list_replacematchingitems_malformed_pair_is_eval_error():
    with pytest.raises(EvalError, match="two-item list"):
        evaluate("List.ReplaceMatchingItems({1, 2}, {{1, 2, 3}})")


# --------------------------------------------------------------------------
# List.RemoveRange / List.ReplaceRange
# --------------------------------------------------------------------------


def test_list_removerange_matches_docs_example():
    result = evaluate("List.RemoveRange({1, 2, 3, 4, -6, -2, -1, 5}, 4, 3)")
    assert result == [1, 2, 3, 4, 5]


def test_list_removerange_omitted_count_defaults_to_one():
    # The docs' only worked example always passes count explicitly, so this
    # default is a pinned CHOICE (mirroring List.RemoveLastN's own
    # documented "only one item is removed" default for an omitted count),
    # not a verified fact.
    assert evaluate("List.RemoveRange({1, 2, 3, 4, 5}, 2)") == [1, 2, 4, 5]


def test_list_removerange_out_of_range_is_eval_error():
    with pytest.raises(EvalError, match="index out of range"):
        evaluate("List.RemoveRange({1, 2, 3}, -1, 1)")
    with pytest.raises(EvalError, match="exceeds the remaining items"):
        evaluate("List.RemoveRange({1, 2, 3}, 2, 5)")


def test_list_replacerange_matches_docs_example():
    result = evaluate("List.ReplaceRange({1, 2, 7, 8, 9, 5}, 2, 3, {3, 4})")
    assert result == [1, 2, 3, 4, 5]


def test_list_replacerange_out_of_range_is_eval_error():
    with pytest.raises(EvalError, match="index out of range"):
        evaluate("List.ReplaceRange({1, 2, 3}, 5, 0, {9})")
    with pytest.raises(EvalError, match="exceeds the remaining items"):
        evaluate("List.ReplaceRange({1, 2, 3}, 1, 5, {9})")


# --------------------------------------------------------------------------
# List.TransformMany
# --------------------------------------------------------------------------


def test_list_transformmany_matches_docs_example():
    src = """
        List.TransformMany(
            {
                [Name = "Alice", Pets = {"Scruffy", "Sam"}],
                [Name = "Bob", Pets = {"Walker"}]
            },
            each [Pets],
            (person, pet) => [Name = person[Name], Pet = pet]
        )
    """
    assert evaluate(src) == [
        {"Name": "Alice", "Pet": "Scruffy"},
        {"Name": "Alice", "Pet": "Sam"},
        {"Name": "Bob", "Pet": "Walker"},
    ]


# --------------------------------------------------------------------------
# List.MaxN / List.MinN
# --------------------------------------------------------------------------


def test_list_maxn_matches_docs_example_1():
    assert evaluate("List.MaxN({3, 4, 5, -1, 7, 8, 2}, 5)") == [8, 7, 5, 4, 3]


def test_list_maxn_matches_docs_example_2_condition():
    result = evaluate(
        'List.MaxN({"boy", "dog", "pony", "cat", "rabbit", "bat"}, '
        "each Text.Length(_) > 3)"
    )
    assert result == ["rabbit", "pony"]


def test_list_minn_matches_docs_example():
    assert evaluate("List.MinN({3, 4, 5, -1, 7, 8, 2}, 5)") == [-1, 2, 3, 4, 5]


def test_list_maxn_and_minn_condition_semantics_diverge_by_design():
    # Verified from each function's OWN docs page, not assumed symmetric:
    # List.MaxN's page says a condition returns "all items that meet the
    # condition" (a full filter); List.MinN's page says "once an item fails
    # the condition, no further items are considered" (stop at the first
    # miss). Sorted ascending/descending, only "2" in {1,5,2,8,3} matches
    # `_ = 2`, and it is NOT the first item in either sort order - so MaxN
    # still finds it (full filter) while MinN does not (stops immediately).
    assert evaluate("List.MaxN({1, 5, 2, 8, 3}, each _ = 2)") == [2]
    assert evaluate("List.MinN({1, 5, 2, 8, 3}, each _ = 2)") == []


def test_list_minn_null_countorcondition_returns_single_smallest():
    # Only List.MinN's own docs page defines a null countOrCondition ("the
    # single smallest value in the list is returned"); wrapped in a list
    # because the declared return type is `as list`.
    assert evaluate("List.MinN({3, 1, 2}, null)") == [1]


def test_list_maxn_null_countorcondition_is_unsupported():
    # List.MaxN's own docs page never defines this case (unlike MinN's) -
    # refused rather than guessed at.
    with pytest.raises(UnsupportedError, match="undocumented for List.MaxN"):
        evaluate("List.MaxN({3, 1, 2}, null)")


def test_list_maxn_comparisoncriteria_key_selector_sorts_by_transformed_value():
    # comparisonCriteria transforms values "before they're compared" but the
    # ORIGINAL values are returned (mirrors the docs' own culture-aware-date
    # example, which this package cannot reproduce because Date.FromText's
    # Culture option isn't implemented yet - Number.Abs proves the same
    # transform-then-return-original mechanism without that dependency).
    assert evaluate("List.MaxN({-5, 3, -1}, 2, each Number.Abs(_))") == [-5, 3]


def test_list_maxn_comparisoncriteria_must_be_one_argument_key_selector():
    # A two-argument comparer or a {selector, comparer} list is a shape
    # equationCriteria allows elsewhere in this file, but MaxN/MinN's own
    # docs page only ever shows a one-argument key selector - refused.
    with pytest.raises(UnsupportedError, match="one-argument key selector"):
        evaluate("List.MaxN({1, 2, 3}, 2, (a, b) => 0)")
    with pytest.raises(UnsupportedError, match="one-argument key selector"):
        evaluate("List.MaxN({1, 2, 3}, 2, {each _, Comparer.Ordinal})")


def test_list_maxn_includenulls_default_true_errors_on_incomparable_mix():
    # Matches this file's existing List.Max/List.Sort convention: a null
    # left in by includeNulls' True default, compared against a number
    # during sorting, is "not comparable" - the same error those functions
    # already raise, not a new invented null-ordering rule.
    with pytest.raises(EvalError, match="not comparable"):
        evaluate("List.MaxN({5, 3, null, 1}, 4)")


def test_list_maxn_includenulls_false_filters_nulls_first():
    assert evaluate("List.MaxN({5, 3, null, 1}, 4, null, false)") == [5, 3, 1]


def test_list_maxn_and_minn_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate("List.MaxN({1, 2, 3}, -1)")
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate("List.MinN({1, 2, 3}, -1)")


# --------------------------------------------------------------------------
# List.Modes
# --------------------------------------------------------------------------


def test_list_modes_matches_docs_example():
    assert evaluate('List.Modes({"A", 1, 2, 3, 3, 4, 5, 5})') == [3, 5]


def test_list_modes_order_is_first_occurrence_not_value_order():
    # The docs' one example ({"A",1,2,3,3,4,5,5} -> {3,5}) is consistent
    # with BOTH first-occurrence order and value-ascending order (3 occurs
    # before 5, AND 3 < 5), so it cannot verify which rule applies. This
    # input discriminates: 5 occurs before 3, so first-occurrence order
    # gives {5,3} while value-ascending order would give {3,5} - pinning
    # this package's chosen rule (cross-referenced from List.Mode's own
    # first-occurrence grouping, per the comment on List.Modes' definition).
    assert evaluate("List.Modes({5, 5, 3, 3})") == [5, 3]


def test_list_modes_empty_is_eval_error():
    with pytest.raises(EvalError, match="must not be empty"):
        evaluate("List.Modes({})")


# --------------------------------------------------------------------------
# List.Covariance
# --------------------------------------------------------------------------


def test_list_covariance_matches_docs_example():
    result = evaluate("List.Covariance({1, 2, 3}, {1, 2, 3})")
    assert result == pytest.approx(0.66666666666666607)


def test_list_covariance_cross_checked_against_statistics_module():
    # Independent reference per the task rule: statistics.covariance is
    # SAMPLE covariance (divides by n-1); this package's List.Covariance is
    # POPULATION covariance (divides by n, verified above against the docs'
    # own example). population = sample * (n-1) / n is the textbook
    # conversion between the two - computed here, not hand-typed, so the
    # check is independent of this module's own formula.
    xs = [1, 3, 6, 10]
    ys = [2, 4, 5, 9]
    n = len(xs)
    sample_cov = statistics.covariance(xs, ys)
    expected_population_cov = sample_cov * (n - 1) / n
    src = (
        f"List.Covariance({{{', '.join(map(str, xs))}}}, {{{', '.join(map(str, ys))}}})"
    )
    assert evaluate(src) == pytest.approx(expected_population_cov)


def test_list_covariance_mismatched_lengths_is_eval_error():
    with pytest.raises(EvalError, match="same number of values"):
        evaluate("List.Covariance({1, 2}, {1, 2, 3})")


def test_list_covariance_both_empty_is_null():
    assert evaluate("List.Covariance({}, {})") is None


# --------------------------------------------------------------------------
# List.Product
# --------------------------------------------------------------------------


def test_list_product_matches_docs_example():
    assert evaluate("List.Product({1, 2, 3, 3, 4, 5, 5})") == 1800


def test_list_product_cross_checked_against_math_prod():
    numbers = [2, 3, 5, 7, 11]
    expected = math.prod(numbers)
    src = "List.Product({" + ", ".join(map(str, numbers)) + "})"
    assert evaluate(src) == expected


def test_list_product_skips_nulls_and_all_null_is_null():
    assert evaluate("List.Product({2, null, 3})") == 6
    assert evaluate("List.Product({null, null})") is None
    assert evaluate("List.Product({})") is None


def test_list_product_precision_decimal_avoids_binary_float_drift():
    # 0.1 * 0.2 * 0.3 in plain binary float is 0.006000000000000001 (verified
    # directly in Python); decimal.Decimal string-round-tripped multiplication
    # gives the exact 0.006 - a real, exactly implementable difference, not
    # an approximation.
    assert evaluate("List.Product({0.1, 0.2, 0.3})") != 0.006
    assert evaluate("List.Product({0.1, 0.2, 0.3}, 1)") == 0.006


def test_list_product_invalid_precision_is_eval_error():
    with pytest.raises(EvalError, match="Precision.Double or Precision.Decimal"):
        evaluate("List.Product({1, 2}, 5)")


# --------------------------------------------------------------------------
# List.Dates / List.DateTimes / List.DateTimeZones / List.Durations /
# List.Times
# --------------------------------------------------------------------------


def test_list_dates_matches_docs_example():
    result = evaluate("List.Dates(#date(2011, 12, 31), 5, #duration(1, 0, 0, 0))")
    assert result == [
        evaluate("#date(2011, 12, 31)"),
        evaluate("#date(2012, 1, 1)"),
        evaluate("#date(2012, 1, 2)"),
        evaluate("#date(2012, 1, 3)"),
        evaluate("#date(2012, 1, 4)"),
    ]


def test_list_dates_rejects_a_datetime_start():
    with pytest.raises(EvalError, match="start must be a date"):
        evaluate("List.Dates(#datetime(2020, 1, 1, 0, 0, 0), 2, #duration(1,0,0,0))")


def test_list_dates_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate("List.Dates(#date(2020, 1, 1), -1, #duration(1,0,0,0))")


def test_list_dates_hits_step_budget_on_huge_count():
    with pytest.raises(EvalError, match="max_steps"):
        evaluate(
            "List.Dates(#date(2020,1,1), 10000000000, #duration(1,0,0,0))",
            max_steps=1000,
        )


def test_list_datetimes_matches_docs_example():
    result = evaluate(
        "List.DateTimes(#datetime(2011, 12, 31, 23, 55, 0), 10, #duration(0, 0, 1, 0))"
    )
    assert result[0] == evaluate("#datetime(2011, 12, 31, 23, 55, 0)")
    assert result[-1] == evaluate("#datetime(2012, 1, 1, 0, 4, 0)")
    assert len(result) == 10


def test_list_datetimes_rejects_a_date_start():
    with pytest.raises(EvalError, match="start must be a datetime"):
        evaluate("List.DateTimes(#date(2020, 1, 1), 2, #duration(0,1,0,0))")


def test_list_datetimezones_matches_docs_example():
    # datetimezone values ARE representable in this engine already (the
    # #datetimezone literal produces a tz-aware datetime.datetime with a
    # fixed-offset tzinfo - verified directly, see the comment on
    # _list_datetimezones), so this is implemented for real rather than
    # refused.
    result = evaluate(
        "List.DateTimeZones(#datetimezone(2011, 12, 31, 23, 55, 0, -8, 0), "
        "10, #duration(0, 0, 1, 0))"
    )
    assert result[0] == evaluate("#datetimezone(2011, 12, 31, 23, 55, 0, -8, 0)")
    assert result[-1] == evaluate("#datetimezone(2012, 1, 1, 0, 4, 0, -8, 0)")
    assert len(result) == 10
    assert all(value.tzinfo is not None for value in result)


def test_list_datetimezones_rejects_a_naive_datetime_start():
    # The task's stated fallback (refuse if datetimezone values don't exist)
    # does not apply here - they do exist - but a NAIVE datetime is still
    # the wrong M type (datetime, not datetimezone) and is refused.
    with pytest.raises(EvalError, match="start must be a datetimezone"):
        evaluate(
            "List.DateTimeZones(#datetime(2011,12,31,23,55,0), 2, #duration(0,0,1,0))"
        )


def test_list_durations_matches_docs_example():
    result = evaluate("List.Durations(#duration(0, 1, 0, 0), 5, #duration(0, 1, 0, 0))")
    assert result == [
        evaluate("#duration(0, 1, 0, 0)"),
        evaluate("#duration(0, 2, 0, 0)"),
        evaluate("#duration(0, 3, 0, 0)"),
        evaluate("#duration(0, 4, 0, 0)"),
        evaluate("#duration(0, 5, 0, 0)"),
    ]


def test_list_times_matches_docs_example():
    result = evaluate("List.Times(#time(12, 0, 0), 4, #duration(0, 1, 0, 0))")
    assert result == [
        evaluate("#time(12, 0, 0)"),
        evaluate("#time(13, 0, 0)"),
        evaluate("#time(14, 0, 0)"),
        evaluate("#time(15, 0, 0)"),
    ]


def test_list_times_wraps_past_midnight():
    # Forced by the `time` type itself (it cannot hold >=24h or <0h), not a
    # stylistic choice among alternatives - the docs' own example never
    # crosses midnight, so this is pinned by this test rather than a
    # worked example.
    result = evaluate("List.Times(#time(23, 59, 59), 3, #duration(0, 0, 0, 2))")
    assert result == [
        evaluate("#time(23, 59, 59)"),
        evaluate("#time(0, 0, 1)"),
        evaluate("#time(0, 0, 3)"),
    ]


def test_list_times_rejects_a_duration_start():
    with pytest.raises(EvalError, match="start must be a time"):
        evaluate("List.Times(#duration(0,1,0,0), 2, #duration(0,1,0,0))")


# --------------------------------------------------------------------------
# List.Random
# --------------------------------------------------------------------------


def test_list_random_returns_the_requested_count_in_range():
    result = evaluate("List.Random(5)")
    assert len(result) == 5
    assert all(0 <= value < 1 for value in result)


def test_list_random_same_seed_is_reproducible():
    # This package's documented CONTRACT (not the docs' own specific
    # numbers, which no third-party implementation can reproduce -
    # Microsoft has never published the underlying RNG algorithm): the SAME
    # seed always yields the SAME list.
    assert evaluate("List.Random(5, 2)") == evaluate("List.Random(5, 2)")


def test_list_random_different_seeds_differ():
    assert evaluate("List.Random(5, 2)") != evaluate("List.Random(5, 3)")


def test_list_random_negative_count_is_eval_error():
    with pytest.raises(EvalError, match="count must not be negative"):
        evaluate("List.Random(-1)")


def test_list_random_hits_step_budget_on_huge_count():
    with pytest.raises(EvalError, match="max_steps"):
        evaluate("List.Random(10000000000)", max_steps=1000)
