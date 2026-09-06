"""Run Microsoft's own worked examples. The corpus nobody here authored.

Four releases shipped a bug that a thousand green tests could not see:
`#table`, `#(lf)`, `Source{[Item=...]}`, and an operator layer that handled
only numbers. Every one was found by running real Power Query M, and every
one survived the suite for the same reason - the suite is written by the
people who wrote the code, in the idioms those people reach for. A library
author writes `{1, 2, 3}`; the reference writes `{1..10}`.

So this file runs 784 examples harvested from the reference itself
(`scripts/harvest_doc_examples.py` builds `fixtures/doc-examples.json`) and
holds three properties that no test of a function's own behaviour can hold:

1. No example may die on an UNKNOWN IDENTIFIER. That means a name real M
   uses is absent from the registry.
2. No example may fail to PARSE.
3. Every example whose printed Output is itself evaluable M must produce
   exactly that value.

Adding it found, in one run: the `..` range operator (23 examples), `is`,
`as`, `??`, `error`, field projection, `Number.PI`, the whole of
`RoundingMode.*`/`TextEncoding.*`/`PercentileMode.*`, `JoinKind.LeftSemi`
and `RightSemi`, a `Uri.Parts` that put the host in the Path field, a
`Table.RemoveMatchingRows` that accepted a comparer and ignored it, a
`Table.Join` that emitted a duplicate key column, and uppercase hex.

Every exemption below carries the reason it is exempt. An entry without one
is how a gate quietly stops being a gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pqtools import evaluate
from pqtools.core import MQueryError

CORPUS = Path(__file__).parent / "fixtures" / "doc-examples.json"

# Examples that cannot run, with the reason. Both are defects in the page.
UNRUNNABLE: dict[tuple[str, int], str] = {
    ("List.Generate", 1): (
        "the example's record initialiser reads a bare `x` that nothing in "
        "scope defines (only `[x]`, the field, exists). It does not run in "
        "Power Query either."
    ),
    ("Table.ApproximateRowCount", 0): (
        "the example calls `sqlTable`, a name the page never defines - "
        "shorthand for 'a table from SQL', not runnable M."
    ),
    ("Json.Document", 2): (
        "the page's M has an unbalanced parenthesis: Json.Document( is "
        "opened once and closed twice. A typo in the source, not a parser "
        "gap."
    ),
    ("Table.TransformColumnTypes", 2): (
        "the page writes `type table [Date = date, Customer ID = text, ...]`"
        " - `Customer ID` is an unquoted identifier containing a space, "
        'which M requires to be written `#"Customer ID"`.'
    ),
}

# Examples that run but disagree with the printed Output, with the reason.
KNOWN_DIVERGENCES: dict[tuple[str, int], str] = {
    ("Csv.Document", 2): (
        "padding a short row: this page's prose says the extra columns "
        '"will be null" and this example prints "" instead. pqtools follows '
        "the normative sentence, which is also what the rest of the "
        "evaluator uses for absent data. A deliberate choice against one "
        "example, not an oversight - see _connectors.py."
    ),
    ("Number.Mod", 1): (
        "the page prints 0.0999999999999994 where the IEEE double is "
        "0.09999999999999942. Sixteen significant digits in the doc against "
        "seventeen in the value: the page truncated, the arithmetic agrees."
    ),
    ("Table.Join", 0): (
        "same rows, different order - verified as a multiset. The doc's "
        "output is ordered by the right table; pqtools groups by the left. "
        "Table.Join's row order is not documented anywhere, and the "
        "joinAlgorithm parameter exists precisely because it varies, so "
        "pinning an order here would assert something M does not promise."
    ),
}


# --------------------------------------------------------------------------
# Examples whose printed Output cannot be COMPARED, and why.
#
# The output test used to `return` when the usage did not evaluate, and again
# when the Output was not evaluable M. Both were silent. 21 examples took one
# of those exits, so a regression that turned a matching example into an
# honest-looking refusal changed nothing anyone could see - the case simply
# stopped being checked, and the only thing that would have noticed was a
# match floor set 31 below the real count.
#
# Each exit is now a recorded fact with a reason. A case that recovers fails
# here asking to be removed, so the list can only shrink.
# --------------------------------------------------------------------------

_CULTURE = (
    "the example pins a non-invariant culture. pqtools implements the "
    "invariant/en-US culture only and refuses the rest BY NAME, because a "
    "wrong separator turns 1.234 into 1234 - a silently wrong number, not a "
    "cosmetic difference."
)
_MASHUP = (
    "the function is evaluated by Power Query's Mashup Engine itself, not by "
    "the M standard library. There is nothing here to run it with, and this "
    "package does not claim Mashup Engine compatibility."
)
_FUZZY = (
    "approximate matching. Microsoft does not document the similarity "
    "algorithm precisely enough to reproduce, and a plausible-looking "
    "approximation is exactly the answer a user cannot check."
)
_NOT_IMPLEMENTED = (
    "not implemented. The refusal is typed and names the function, but this "
    "is a genuine gap in coverage, not a defect in the page."
)

NO_OUTPUT_COMPARISON: dict[tuple[str, int], str] = {
    ("Date.From", 2): _CULTURE,
    ("Date.FromText", 3): _CULTURE,
    ("Excel.Workbook", 0): (
        "the example reads C:\\Book1.xlsx, a file on the machine that wrote "
        "the page. Nothing here can supply it."
    ),
    ("Function.ScalarVector", 1): (
        "the example's Table.TransformColumnTypes target is `type record`, a "
        "structured target this build does not convert to."
    ),
    ("ItemExpression.From", 0): _MASHUP,
    ("Json.Document", 0): (
        "the page's printed Output is pretty-printed for reading and is not "
        "parseable M, so there is no value to compare against. The usage "
        "itself runs."
    ),
    ("List.MaxN", 2): _CULTURE,
    ("RowExpression.Column", 0): _MASHUP,
    ("RowExpression.From", 0): _MASHUP,
    ("Table.AddFuzzyClusterColumn", 0): _FUZZY,
    ("Table.FuzzyGroup", 0): _FUZZY,
    ("Table.FuzzyJoin", 0): _FUZZY,
    ("Table.FuzzyNestedJoin", 0): _FUZZY,
    ("Table.RemoveRowsWithErrors", 0): _NOT_IMPLEMENTED,
    ("Table.ReplaceErrorValues", 0): _NOT_IMPLEMENTED,
    ("Table.ReplaceErrorValues", 1): _NOT_IMPLEMENTED,
    ("Table.TransformColumnTypes", 1): _CULTURE,
    ("Table.TransformColumnTypes", 2): (
        "the page's M has an unbalanced parenthesis; see UNRUNNABLE above. "
        "A typo in the source, not a parser gap."
    ),
    ("Table.TransformColumnTypes", 3): _CULTURE,
    ("Text.From", 4): _CULTURE,
    ("Web.Headers", 0): (
        "a data-source connector needing vendor credentials or a proprietary "
        "driver. It refuses by name rather than pretending."
    ),
}


def _corpus() -> list[tuple[str, int, dict[str, str]]]:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))["functions"]
    return [
        (name, index, example)
        for name, examples in sorted(data.items())
        for index, example in enumerate(examples)
    ]


CASES = _corpus()


# Each example is evaluated by two parametrised tests and the match count.
# Memoising turns three passes over 784 examples into one; without it this
# file alone was longer than the rest of the suite put together, which is
# how a gate gets skipped.
_RESULTS: dict[str, tuple[bool, Any]] = {}


def _run(source: str) -> tuple[bool, Any]:
    if source not in _RESULTS:
        try:
            _RESULTS[source] = (True, evaluate(source))
        except MQueryError as error:
            _RESULTS[source] = (False, f"{type(error).__name__}: {error}")
    return _RESULTS[source]


def test_the_corpus_is_present_and_has_not_shrunk() -> None:
    """A corpus that quietly emptied would disarm every test below.

    The floor is well under the current count so an upstream page losing an
    example is not a build break, but losing the file - or most of it - is.
    """
    assert len(CASES) > 700, (
        f"only {len(CASES)} examples in {CORPUS.name}; re-run "
        "scripts/harvest_doc_examples.py"
    )


@pytest.mark.parametrize(
    ("name", "index", "example"),
    [pytest.param(*case, id=f"{case[0]}#{case[1]}") for case in CASES],
)
def test_a_documented_example_neither_parses_wrong_nor_names_an_unknown(
    name: str, index: int, example: dict[str, str]
) -> None:
    """The two failures that always mean a real gap.

    A typed refusal is fine here - a connector this package will not run, a
    culture it does not implement, an example that needs a file. Those are
    honest answers. "unknown identifier" is not: it means real M uses a name
    the registry has never heard of. Neither is a parse error.
    """
    ok, result = _run(example["usage"])
    if ok:
        return
    reason = UNRUNNABLE.get((name, index))
    for symptom in ("unknown identifier", "ParseError"):
        if symptom in str(result):
            assert reason is not None, (
                f"{name} example {index} fails with `{result}`.\n"
                "A documented example must not hit that: it means real "
                "Power Query M uses something this evaluator does not have. "
                "Implement it, or - only if the example itself is broken - "
                "add it to UNRUNNABLE with the reason."
            )


@pytest.mark.parametrize(
    ("name", "index", "example"),
    [
        pytest.param(*case, id=f"{case[0]}#{case[1]}")
        for case in CASES
        if case[2]["output"]
    ],
)
def test_a_documented_example_produces_its_documented_output(
    name: str, index: int, example: dict[str, str]
) -> None:
    """Where the page prints an answer, that answer is the assertion."""
    ran, got = _run(example["usage"])
    printed, want = _run(example["output"])
    exempt = NO_OUTPUT_COMPARISON.get((name, index))
    if exempt is not None:
        assert not (ran and printed), (
            f"{name} example {index} now evaluates AND its Output parses, so "
            f"it can be compared - but it is still exempt as: {exempt}\n"
            "Delete the NO_OUTPUT_COMPARISON entry; the case is checked now."
        )
        return
    assert ran, (
        f"{name} example {index} no longer evaluates: {got}\n"
        "It used to be compared against its documented output. Fix the "
        "regression, or - only if the example genuinely cannot be run here - "
        "add it to NO_OUTPUT_COMPARISON with the reason."
    )
    assert printed, (
        f"{name} example {index}: its documented Output no longer parses as "
        f"M: {want}\nAdd it to NO_OUTPUT_COMPARISON with the reason if the "
        "page's Output is genuinely not a value."
    )
    divergence = KNOWN_DIVERGENCES.get((name, index))
    if divergence is not None:
        assert got != want, (
            f"{name} example {index} now MATCHES its documented output, but "
            f"is still listed in KNOWN_DIVERGENCES as: {divergence}\n"
            "Delete the entry."
        )
        return
    assert got == want, (
        f"{name} example {index} disagrees with its own reference page.\n"
        f"  got:  {got!r}\n  want: {want!r}\n"
        "Fix the implementation, or - only if the divergence is deliberate "
        "and defensible - add it to KNOWN_DIVERGENCES with the reason."
    )


# The measured number of documented examples that reproduce their printed
# output, kept in tests/fixtures/doc-example-matches.json so that
# scripts/sync_builtin_list.py can read it without importing a pytest module.
# SUPPORT-MATRIX.md, README.md and llms.txt all state it, and all three read
# it from that one file - `141` was previously typed into four files by hand,
# and nothing compared them.
#
# It is asserted for EQUALITY below, not as a floor. A floor cannot tell a
# regression from an improvement: the old `>= 110` sat 31 below reality, so a
# third of the matches could have died silently. Equality means the number
# moving in either direction stops the build and the documents get updated in
# the same commit.
_MATCHES = CORPUS.parent / "doc-example-matches.json"
try:
    DOCUMENTED_MATCHES: int = int(
        json.loads(_MATCHES.read_text(encoding="utf-8"))["reproducing_exactly"]
    )
except (OSError, ValueError, KeyError, TypeError) as error:
    raise RuntimeError(
        f"{_MATCHES}: missing or malformed ({error!r}). It is maintained by "
        "hand, asserted for equality by this module, and read by "
        "scripts/sync_builtin_list.py."
    ) from error


def test_enough_examples_actually_match_to_mean_something() -> None:
    """A floor on the matches, because the two tests above are one-sided.

    Both pass when an example merely refuses in a typed way. If a regression
    turned half the library into honest refusals they would stay green, and
    only this count would notice.
    """
    matched = 0
    for _name, _index, example in CASES:
        if not example["output"]:
            continue
        ran, got = _run(example["usage"])
        printed, want = _run(example["output"])
        if ran and printed and got == want:
            matched += 1
    assert matched == DOCUMENTED_MATCHES, (
        f"{matched} documented examples reproduce their printed output; "
        f"DOCUMENTED_MATCHES says {DOCUMENTED_MATCHES}. Fewer is a "
        "regression - find it. More is progress - raise reproducing_exactly in "
        "tests/fixtures/doc-example-matches.json and run "
        "scripts/sync_builtin_list.py so SUPPORT-MATRIX.md, README.md "
        "and llms.txt state the new number in this same commit."
    )
