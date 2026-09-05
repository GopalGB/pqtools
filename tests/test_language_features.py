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

from pqtools import EvalError, UnsupportedError, evaluate
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
        "operator + is not defined for text and number"
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
# @ - M's inclusive identifier reference
# --------------------------------------------------------------------------


def test_at_prefixed_name_resolves_the_binding_being_defined() -> None:
    source = "let f = (n) => if n <= 1 then 1 else n * @f(n - 1) in f(5)"
    assert evaluate(source) == 120


def test_at_prefixed_and_plain_recursion_do_not_agree() -> None:
    """This test used to assert they agree. That was the bug it was hiding.

    M's grammar has two identifier references, and the difference is the
    whole point of the `@`: a plain `f` is EXCLUSIVE and skips the binding
    currently being defined, so it is not recursion at all; `@f` is
    INCLUSIVE and names f itself. That is why every recursive M function in
    the wild carries the `@`.

    Treating them as interchangeable let a query run here and fail in Power
    Query - the one outcome this package exists to prevent.
    """
    at = "let f = (n) => if n <= 1 then 1 else n * @f(n - 1) in f(6)"
    plain = "let f = (n) => if n <= 1 then 1 else n * f(n - 1) in f(6)"
    assert evaluate(at) == 720
    with pytest.raises(UnsupportedError, match="unknown identifier: f"):
        evaluate(plain)


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


# --------------------------------------------------------------------------
# Record scope - a field expression can name its siblings
# --------------------------------------------------------------------------
#
# `[a = 1, b = a + 1]` raised "unknown identifier: a" here for six releases,
# through a suite that tested records constantly. Every one of those tests
# wrote a record of literals or of already-bound names, because that is what
# a test author reaches for when they need "a record". The reference does
# not: `List.Generate`'s second documented example is
#
#     each [x = List.Count([y]), y = [y] & {x}]
#
# where the bare `x` in the second field is the first field and there is
# nothing else in scope by that name. The example was unrunnable.


def test_a_record_field_can_name_a_later_one() -> None:
    """Order must not matter - the fields are lazy, exactly like `let`."""
    assert evaluate("[a = b, b = 1]") == {"a": 1, "b": 1}


def test_a_record_field_can_name_an_earlier_one() -> None:
    assert evaluate("[a = 1, b = a + 1]") == {"a": 1, "b": 2}


def test_a_record_cycle_is_an_error_naming_the_field() -> None:
    """Not a hang, and not a silent fallback to an outer binding."""
    with pytest.raises(EvalError, match="circular reference in 'a'"):
        evaluate("[a = b, b = a]")


def test_a_field_expression_sees_siblings_but_not_the_field_it_defines() -> None:
    """M's exclusive-identifier rule, which three doc pages pin between them.

    A plain `x` skips the binding CURRENTLY BEING DEFINED and resolves
    outward; `@x` names it. Getting this wrong in either direction breaks a
    documented result:

      - fields not in scope at all   -> the List.Generate example below
                                        dies on "unknown identifier: x"
      - fields in scope unconditionally -> `[List.Sum = List.Sum]` becomes
                                        a cycle, and expression-evaluate's
                                        example 2 says the answer is 6

    Both were implemented here, in that order, before the rule that makes
    all three documented outputs true at once was found.
    """
    # A sibling: in scope.
    assert evaluate("let x = 5 in [x = 1, y = x]") == {"x": 1, "y": 1}
    # The field's own name: excluded, so it resolves outward.
    assert evaluate("let x = 5 in [x = x]") == {"x": 5}
    # A name the record does not define at all: resolves outward as ever.
    assert evaluate("let x = 5 in [y = x]") == {"y": 5}
    # With nothing outside to find, the exclusion is an honest error.
    with pytest.raises(UnsupportedError, match="unknown identifier: a"):
        evaluate("[a = a]")


def test_the_expression_evaluate_environment_idiom_works() -> None:
    """expression-evaluate, example 2 verbatim, stated output 6.

    `[List.Sum = List.Sum]` is how M's own docs pass a library function
    into an evaluated expression. Reading it as a cycle would break the
    documented idiom for the whole function.
    """
    assert (
        evaluate('Expression.Evaluate("List.Sum({1, 2, 3})", [List.Sum = List.Sum])')
        == 6
    )


def test_a_let_binding_may_be_defined_from_the_one_it_shadows() -> None:
    """`let a = 1 in let a = a + 1 in a` - the same rule, one level up.

    This used to report a circular reference, because the inner `a` found
    itself instead of the outer one.
    """
    assert evaluate("let a = 1 in let a = a + 1 in a") == 2


def test_the_list_generate_example_from_the_reference_runs() -> None:
    """list-generate, example 2, verbatim. It is the reason this exists."""
    assert evaluate(
        "List.Generate(\n"
        "    () => [x = 1, y = {}],\n"
        "    each [x] < 10,\n"
        "    each [x = List.Count([y]), y = [y] & {x}],\n"
        "    each [x]\n"
        ")"
    ) == [1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_a_nested_record_sees_the_outer_record_it_is_defined_in() -> None:
    assert evaluate("[a = 1, b = [c = a + 1]]") == {"a": 1, "b": {"c": 2}}


# --------------------------------------------------------------------------
# `optional` parameters on a user-defined function
# --------------------------------------------------------------------------


def test_an_optional_parameter_may_be_omitted() -> None:
    """`(x, optional y) => ...` called with one argument.

    Every parameter was treated as required, so the plainest custom
    function with a default - the shape an author writes the moment one
    argument is not always wanted - failed at the call site with an arity
    error. The `optional` keyword parses as a Constant, and `_semantic`
    filters Constants out, so the name and the declared type both read
    correctly and only the flag was silently dropped.
    """
    assert evaluate("((x, optional y) => if y = null then x else x + y)(1)") == 1
    assert evaluate("((x, optional y) => if y = null then x else x + y)(1, 2)") == 3


def test_an_omitted_optional_parameter_is_null_inside_the_body() -> None:
    assert evaluate("let f = (a, optional b, optional c) => {a, b, c} in f(1, 2)") == [
        1,
        2,
        None,
    ]


def test_an_optional_parameters_declared_type_still_holds_when_supplied() -> None:
    """`optional y as number` is a NULLABLE number, not an unchecked one.

    Skipping the check entirely would throw away a real guarantee; applying
    it to the absence would reject the very thing the parameter exists to
    allow.
    """
    assert evaluate("((x, optional y as number) => x)(1)") == 1
    with pytest.raises(EvalError, match="argument 'y': expected number, got text"):
        evaluate('((x, optional y as number) => x)(1, "a")')


def test_too_many_arguments_still_names_the_range() -> None:
    with pytest.raises(EvalError, match=r"expects between 1 and 2 argument"):
        evaluate("((x, optional y) => x)(1, 2, 3)")
    with pytest.raises(EvalError, match=r"expects 1 argument"):
        evaluate("((x) => x)()")


# --------------------------------------------------------------------------
# The three primitive type names whose ascription was wrong
# --------------------------------------------------------------------------
#
# `any`, `number`, `text` and friends were right. These three were not, and
# each was wrong in a different direction - which is why testing one of them
# would not have found the others.


def test_nullable_adds_null_it_does_not_remove_the_check() -> None:
    """`nullable number` is number-or-null, not "anything".

    The declaration used to be discarded the moment the parser's `nullable`
    Constant was seen, on the reasoning that nullable "only adds null" and
    the checker should stay out of the way. So `(x as nullable number)` was
    the ONE spelling in the language that quietly accepted text - the
    strictest-looking parameter list in a query was the least enforced.
    """
    assert evaluate("let f = (x as nullable number) => x in f(1)") == 1
    assert evaluate("let f = (x as nullable number) => x in f(null)") is None
    with pytest.raises(EvalError, match="expected nullable number, got text"):
        evaluate('let f = (x as nullable number) => x in f("oops")')


def test_a_nullable_return_type_is_enforced_the_same_way() -> None:
    assert evaluate("let f = (x) as nullable number => null in f(1)") is None
    with pytest.raises(EvalError, match="return value: expected nullable number"):
        evaluate('let f = (x) as nullable number => "oops" in f(1)')


def test_type_none_holds_no_values_at_all() -> None:
    """It was grouped with `any`, which is its exact opposite.

    `any` holds every value; `none` holds none, so `(x as none) => x` can
    never be called successfully - including with null.
    """
    with pytest.raises(EvalError, match="no value has type none"):
        evaluate("let f = (x as none) => x in f(1)")
    with pytest.raises(EvalError, match="expected none, got null"):
        evaluate("let f = (x as none) => x in f(null)")


def test_type_null_accepts_null_which_is_the_only_thing_it_accepts() -> None:
    """It used to REJECT null: "expected null, got null".

    The null branch ran before the declared name was read, so the one value
    the type contains was the one value it turned away.
    """
    assert evaluate("let f = (x as null) => x in f(null)") is None
    with pytest.raises(EvalError, match="expected null, got number"):
        evaluate("let f = (x as null) => x in f(1)")


def test_anynonnull_is_any_minus_null() -> None:
    assert evaluate("let f = (x as anynonnull) => x in f(1)") == 1
    with pytest.raises(EvalError, match="expected anynonnull, got null"):
        evaluate("let f = (x as anynonnull) => x in f(null)")


# --------------------------------------------------------------------------
# try ... catch - which handler form, decided by the signature
# --------------------------------------------------------------------------


def test_catch_accepts_both_handler_arities() -> None:
    assert evaluate('try error "boom" catch () => "handled"') == "handled"
    assert evaluate('try error "boom" catch (e) => e[Message]') == "boom"
    assert evaluate("try 1 + 1 catch (e) => 0") == 2


def test_an_error_inside_the_handler_is_not_replaced_by_an_arity_error() -> None:
    """The handler form used to be guessed by re-running it.

    The one-argument call was tried, and if the failure text contained the
    word "argument" the handler was called AGAIN with none. So a perfectly
    good one-argument handler whose own body raised
    `argument 'x': expected number, got text` reported
    `function expects 1 argument(s), got 0` instead - the real error
    replaced by one about the machinery that hid it. The arity now comes
    off the closure's parameter list, which is where it was all along.
    """
    with pytest.raises(EvalError, match="argument 'x': expected number, got text"):
        evaluate(
            "let inner = (x as number) => x in "
            'try error "boom" catch (e) => inner("not a number")'
        )
