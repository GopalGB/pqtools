"""M language forms that used to raise UnsupportedError.

Each of these was refused for the same bad reason: it was easier to name the
construct than to implement it. None of them needed Microsoft's engine.

The `try` family is the one that mattered most. Only `try x otherwise y`
worked, which forces every caller to pick a sentinel value and then makes it
impossible to tell a real sentinel in the data from a failure.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pqtools import EvalError, evaluate
from pqtools.io import IOBlockedError, IOPolicy

# --------------------------------------------------------------------------
# try - all four forms
# --------------------------------------------------------------------------


def test_bare_try_on_success_reports_the_value() -> None:
    assert evaluate("try 1 + 1") == {"HasError": False, "Value": 2}


def test_bare_try_on_failure_reports_an_error_record() -> None:
    result = evaluate('try "a" + 1')
    assert result["HasError"] is True
    assert result["Error"]["Reason"] == "Expression.Error"
    assert "number" in result["Error"]["Message"]


def test_bare_try_distinguishes_a_failure_from_a_sentinel_value() -> None:
    # The reason the bare form has to exist: with only `otherwise -1`, a
    # genuine -1 in the data is indistinguishable from a failed step.
    assert evaluate("try -1") == {"HasError": False, "Value": -1}


def test_catch_receives_the_error_record() -> None:
    assert evaluate('try "a" + 1 catch (e) => e[Message]') == (
        "expected a number, got text"
    )


def test_catch_accepts_the_zero_argument_form() -> None:
    assert evaluate('try "a" + 1 catch () => "failed"') == "failed"


def test_catch_is_not_entered_when_nothing_fails() -> None:
    assert evaluate('try 41 + 1 catch (e) => "wrong"') == 42


def test_otherwise_still_works() -> None:
    assert evaluate('try "a" + 1 otherwise -1') == -1


@pytest.mark.parametrize("form", ["otherwise 0", "catch (e) => 0"])
def test_no_handler_swallows_a_policy_block(form: str) -> None:
    # A blocked connector is a permission decision, not a data error. If a
    # handler caught it the caller would receive 0 and never learn the fetch
    # did not happen, which is the worst outcome this package can produce.
    with pytest.raises(IOBlockedError):
        evaluate(f'try Web.Contents("https://example.com") {form}')


def test_no_handler_swallows_an_unsupported_construct() -> None:
    from pqtools import UnsupportedError

    with pytest.raises(UnsupportedError):
        evaluate("try SharePoint.Files(1) otherwise 0")


def test_a_handler_still_catches_ordinary_data_errors() -> None:
    # The flip side: the guard above must not make `try` useless for the
    # thing it is actually for.
    assert evaluate('try ("a" + 1) otherwise 0') == 0


# --------------------------------------------------------------------------
# @ outer-scope reference
# --------------------------------------------------------------------------


def test_at_prefixed_name_resolves_the_enclosing_binding() -> None:
    source = "let f = (n) => if n <= 1 then 1 else n * @f(n - 1) in f(5)"
    assert evaluate(source) == 120


def test_at_prefixed_and_plain_recursion_agree() -> None:
    plain = "let f = (n) => if n <= 1 then 1 else n * f(n - 1) in f(6)"
    at = "let f = (n) => if n <= 1 then 1 else n * @f(n - 1) in f(6)"
    assert evaluate(plain) == evaluate(at) == 720


# --------------------------------------------------------------------------
# Type ascription - declared and enforced
# --------------------------------------------------------------------------


def test_parameter_ascription_accepts_a_matching_argument() -> None:
    assert evaluate("let f = (n as number) => n + 1 in f(1)") == 2


@pytest.mark.parametrize(
    ("declared", "call", "expected"),
    [
        ("text", "1", "expected text, got number"),
        ("number", '"x"', "expected number, got text"),
        ("logical", "1", "expected logical, got number"),
    ],
)
def test_parameter_ascription_rejects_a_mismatched_argument(
    declared: str, call: str, expected: str
) -> None:
    with pytest.raises(EvalError, match=expected):
        evaluate(f"let f = (v as {declared}) => v in f({call})")


def test_return_ascription_is_enforced() -> None:
    with pytest.raises(EvalError, match="return value: expected text"):
        evaluate("let f = (n) as text => n + 1 in f(1)")


def test_return_ascription_accepts_a_matching_result() -> None:
    assert evaluate("let f = (n) as number => n + 1 in f(1)") == 2


def test_both_ascriptions_together() -> None:
    assert evaluate("let f = (n as number) as number => n * 2 in f(21)") == 42


def test_as_any_accepts_anything() -> None:
    assert evaluate('let f = (v as any) => 1 in f("x")') == 1


def test_non_nullable_ascription_rejects_null() -> None:
    with pytest.raises(EvalError, match="expected number, got null"):
        evaluate("let f = (n as number) => 1 in f(null)")


def test_nullable_ascription_accepts_null() -> None:
    # `as nullable number` differs from `as number` in exactly one way, so the
    # checker's whole job here is to stay out of the way.
    assert evaluate("let f = (n as nullable number) => 1 in f(null)") == 1


def test_temporal_ascriptions_are_checked() -> None:
    assert evaluate("let f = (d as date) => d in f(#date(2026, 1, 1))") == dt.date(
        2026, 1, 1
    )
    with pytest.raises(EvalError, match="expected date"):
        evaluate('let f = (d as date) => d in f("2026-01-01")')


def test_an_unmodelled_declared_type_does_not_break_the_function() -> None:
    # A table type is declared but not checkable here. Refusing the whole
    # function would lose a working query over a type the checker cannot read.
    source = 'let f = (t as table) => Table.RowCount(t) in f(#table({"a"}, {{1}}))'
    assert evaluate(source) == 1


# --------------------------------------------------------------------------
# #shared / #sections
# --------------------------------------------------------------------------


def test_hash_shared_names_the_scope_it_needs() -> None:
    from pqtools import UnsupportedError

    with pytest.raises(UnsupportedError, match="section document"):
        evaluate("#shared")


def test_policy_default_still_denies_after_all_this() -> None:
    # A regression guard for the whole feature set: none of the language work
    # above may quietly widen what an evaluation is allowed to reach.
    with pytest.raises(IOBlockedError):
        evaluate('let S = Web.Contents("https://example.com") in S')
    assert IOPolicy().allow_net is False
    assert IOPolicy().allow_db is False
