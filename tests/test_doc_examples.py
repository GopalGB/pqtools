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
    if not ran:
        return  # covered by the test above
    printed, want = _run(example["output"])
    if not printed:
        return  # the Output is prose, or M this evaluator cannot run
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
    assert matched >= 110, (
        f"only {matched} documented examples reproduce their printed output; "
        "that number has only ever gone up"
    )
