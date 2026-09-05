"""``Number.*`` and ``Logical.*`` functions missing until this session's
Microsoft Learn diff.

Found by fetching the real number-functions.md / logical-functions.md
tables and diffing them against the live registry, per the task brief -
not by memory. Every numeric function here is cross-checked against
Python's own ``math`` module in the test (per the task's Number-semantics
rule), and every place .NET's documented behaviour differs from Python's
default is called out in the test that pins it, not just in the
implementation comment.
"""

from __future__ import annotations

import math

import pytest

from pqtools.evaluate import EvalError, evaluate

# --------------------------------------------------------------------------
# Number.Combinations / Number.Permutations
# --------------------------------------------------------------------------


def test_number_combinations_matches_ms_docs_example():
    assert evaluate("Number.Combinations(5, 3)") == math.comb(5, 3) == 10


def test_number_permutations_matches_ms_docs_example():
    assert evaluate("Number.Permutations(5, 3)") == math.perm(5, 3) == 60


def test_number_combinations_null_propagates():
    assert evaluate("Number.Combinations(null, 3)") is None
    assert evaluate("Number.Combinations(5, null)") is None


def test_number_combinations_size_larger_than_set_is_zero_not_an_error():
    # math.comb/math.perm both give 0 (not an error) when the requested
    # group size exceeds the set - the standard nCr/nPr convention,
    # cross-checked against Python's own math module.
    assert evaluate("Number.Combinations(3, 5)") == math.comb(3, 5) == 0
    assert evaluate("Number.Permutations(3, 5)") == math.perm(3, 5) == 0


def test_number_combinations_negative_set_size_is_eval_error():
    with pytest.raises(EvalError, match="Number.Combinations"):
        evaluate("Number.Combinations(-1, 2)")


def test_number_permutations_negative_permutation_size_is_eval_error():
    with pytest.raises(EvalError, match="Number.Permutations"):
        evaluate("Number.Permutations(5, -1)")


# --------------------------------------------------------------------------
# Trigonometry
# --------------------------------------------------------------------------


def test_number_cos_matches_ms_docs_examples():
    assert evaluate("Number.Cos(0)") == 1
    assert evaluate(f"Number.Cos({math.pi!r})") == pytest.approx(-1)


def test_number_sin_matches_ms_docs_example():
    assert evaluate("Number.Sin(0)") == 0


def test_number_tan_matches_ms_docs_example():
    assert evaluate("Number.Tan(1)") == pytest.approx(1.5574077246549023)
    assert evaluate("Number.Tan(1)") == math.tan(1)


def test_number_atan_cross_checked_against_python_math():
    assert evaluate("Number.Atan(1)") == math.atan(1)


def test_number_atan2_argument_order_is_y_then_x():
    # Number.Atan2(y, x) - cross-checked against Python's math.atan2(y, x),
    # same argument order and same IEEE-754 atan2 convention as .NET's
    # Math.Atan2(y, x).
    assert evaluate("Number.Atan2(1, 1)") == math.atan2(1, 1)
    assert (
        evaluate("Number.Atan2(0, -1)") == math.atan2(0, -1) == pytest.approx(math.pi)
    )
    assert evaluate("Number.Atan2(0, 0)") == 0


def test_trig_functions_null_propagate():
    for name in ("Acos", "Asin", "Atan", "Cos", "Sin", "Tan", "Cosh", "Sinh", "Tanh"):
        assert evaluate(f"Number.{name}(null)") is None
    assert evaluate("Number.Atan2(null, 1)") is None
    assert evaluate("Number.Atan2(1, null)") is None


def test_number_acos_in_domain_matches_python_math():
    assert evaluate("Number.Acos(0.5)") == math.acos(0.5)
    assert evaluate("Number.Acos(1)") == math.acos(1) == 0


def test_number_asin_in_domain_matches_python_math():
    assert evaluate("Number.Asin(0.5)") == math.asin(0.5)


def test_number_acos_out_of_domain_is_nan_not_an_error():
    # .NET vs Python trap: Math.Acos/Math.Asin return NaN outside [-1, 1];
    # Python's math.acos/math.asin RAISE ValueError instead (cross-checked
    # directly against Python's own math module below). This module
    # follows the .NET/documented-elsewhere-in-this-file convention
    # (Number.Sqrt(-1) is already NaN, not an error) rather than Python's.
    with pytest.raises(ValueError):
        math.acos(2)
    result = evaluate("Number.Acos(2)")
    assert isinstance(result, float) and math.isnan(result)


def test_number_asin_out_of_domain_is_nan_not_an_error():
    with pytest.raises(ValueError):
        math.asin(-2)
    result = evaluate("Number.Asin(-2)")
    assert isinstance(result, float) and math.isnan(result)


def test_number_cosh_and_sinh_match_python_math_in_range():
    assert evaluate("Number.Cosh(1)") == math.cosh(1)
    assert evaluate("Number.Sinh(1)") == math.sinh(1)


def test_number_cosh_overflow_is_infinity_not_an_error():
    # .NET vs Python trap: Python's math.cosh/math.sinh RAISE OverflowError
    # for a large-magnitude input where .NET's Math.Cosh/Math.Sinh return
    # +/-Infinity - cross-checked directly against Python's own math module.
    with pytest.raises(OverflowError):
        math.cosh(1000)
    assert evaluate("Number.Cosh(1000)") == math.inf
    assert evaluate("Number.Cosh(-1000)") == math.inf


def test_number_sinh_overflow_is_signed_infinity_not_an_error():
    with pytest.raises(OverflowError):
        math.sinh(1000)
    assert evaluate("Number.Sinh(1000)") == math.inf
    assert evaluate("Number.Sinh(-1000)") == -math.inf


def test_number_tanh_does_not_overflow():
    # Bounded to (-1, 1); Python does not raise for a large input here,
    # unlike Cosh/Sinh (cross-checked against Python's own math.tanh).
    assert evaluate("Number.Tanh(1000)") == math.tanh(1000) == 1.0
    assert evaluate("Number.Tanh(0)") == 0


# --------------------------------------------------------------------------
# Number.BitwiseNot / BitwiseShiftLeft / BitwiseShiftRight
# --------------------------------------------------------------------------


def test_number_bitwise_not_matches_twos_complement_identity():
    # ~x == -x - 1 in two's complement, width-independent - cross-checked
    # against Python's own `~` operator (which computes the same identity).
    assert evaluate("Number.BitwiseNot(0)") == ~0 == -1
    assert evaluate("Number.BitwiseNot(5)") == ~5 == -6
    assert evaluate("Number.BitwiseNot(-1)") == ~(-1) == 0


def test_number_bitwise_not_null_propagates():
    assert evaluate("Number.BitwiseNot(null)") is None


def test_number_bitwise_shift_left_matches_python_shift():
    assert evaluate("Number.BitwiseShiftLeft(1, 3)") == 1 << 3 == 8
    assert evaluate("Number.BitwiseShiftLeft(1, 0)") == 1


def test_number_bitwise_shift_right_matches_python_arithmetic_shift():
    # Python's `>>` on a signed int is an arithmetic shift (sign-
    # preserving), matching .NET's `>>` on a signed integer exactly for a
    # non-negative shift count - cross-checked against Python's own `>>`.
    assert evaluate("Number.BitwiseShiftRight(8, 3)") == 8 >> 3 == 1
    assert evaluate("Number.BitwiseShiftRight(-8, 1)") == -8 >> 1 == -4


def test_number_bitwise_shift_null_propagates():
    assert evaluate("Number.BitwiseShiftLeft(null, 1)") is None
    assert evaluate("Number.BitwiseShiftRight(1, null)") is None


def test_number_bitwise_shift_negative_amount_is_unsupported():
    # A choice (see the implementation comment): real .NET masks a negative
    # shift count to a fixed 64-bit width, which this evaluator's
    # arbitrary-precision numbers cannot faithfully reproduce - refused by
    # name rather than guessing a width.
    from pqtools.evaluate import UnsupportedError

    with pytest.raises(UnsupportedError, match="negative shift"):
        evaluate("Number.BitwiseShiftLeft(1, -1)")
    with pytest.raises(UnsupportedError, match="negative shift"):
        evaluate("Number.BitwiseShiftRight(1, -1)")


# --------------------------------------------------------------------------
# Logical.ToText
# --------------------------------------------------------------------------


def test_logical_to_text_matches_ms_docs_example():
    assert evaluate("Logical.ToText(true)") == "true"


def test_logical_to_text_false():
    assert evaluate("Logical.ToText(false)") == "false"


def test_logical_to_text_is_lowercase_unlike_text_from():
    # Trap: Text.From(true) in this same codebase already renders "TRUE"
    # (uppercase) - Logical.ToText renders lowercase "true"/"false". Two
    # different, independently-documented functions; not a contradiction.
    assert evaluate("Logical.ToText(true)") == "true"
    assert evaluate("Text.From(true)") == "TRUE"


def test_logical_to_text_null_propagates():
    assert evaluate("Logical.ToText(null)") is None


def test_logical_to_text_non_logical_is_eval_error():
    with pytest.raises(EvalError, match="Logical.ToText"):
        evaluate('Logical.ToText("true")')
