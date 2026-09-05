"""SUPPORT-MATRIX.md must state numbers the registry actually has.

The documentation drifted because nothing checked it. Three files described
three different releases at once: the README said in one paragraph that
`Sql.Database` "raises a typed error naming itself" and in a table forty lines
later that it "works" (the table was right - the paragraph was left over from a
release where the connectors genuinely refused), while `ops/STATUS.md`
presented 0.1.0 under the package's old name as current.

Prose cannot be trusted to stay true on its own, so the authoritative file is
the one with a test behind it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pqtools.catalog import DOCUMENTED, REASONS
from pqtools.evaluate import BUILTINS

_ROOT = Path(__file__).resolve().parent.parent
_MATRIX = _ROOT / "SUPPORT-MATRIX.md"
_TEXT = _MATRIX.read_text(encoding="utf-8")

_SIGNATURES = json.loads(
    (_ROOT / "tests" / "fixtures" / "m-signatures.json").read_text(encoding="utf-8")
)
_ENUMS = json.loads(
    (_ROOT / "tests" / "fixtures" / "m-enum-values.json").read_text(encoding="utf-8")
)

_REGISTERED = set(BUILTINS)
_CALLABLE = {name for name, value in BUILTINS.items() if callable(value)}


def _stated(label: str) -> int:
    """The number in the `| <label> | <number> |` row."""
    pattern = re.compile(
        r"^\|\s*" + re.escape(label) + r"\s*\|\s*([\d,]+)\s*\|", re.MULTILINE
    )
    match = pattern.search(_TEXT)
    assert match is not None, f"SUPPORT-MATRIX.md has no row labelled {label!r}"
    return int(match.group(1).replace(",", ""))


@pytest.mark.parametrize(
    ("label", "actual"),
    [
        ("Names in Microsoft's function reference", len(DOCUMENTED)),
        ("... of those, registered here", len(set(DOCUMENTED) & _REGISTERED)),
        (
            "... of those, refused by name with a reason",
            len(set(DOCUMENTED) - _REGISTERED),
        ),
        ("Total registry entries", len(_REGISTERED)),
        ("Signatures verified against the reference", len(_SIGNATURES)),
        ("Enum values verified against the reference", len(_ENUMS)),
    ],
)
def test_a_stated_count_matches_the_registry(label: str, actual: int) -> None:
    assert _stated(label) == actual, (
        f"SUPPORT-MATRIX.md says {_stated(label)} for {label!r}; the real "
        f"number is {actual}. Update the file - it is the authoritative one."
    )


def test_the_registry_split_adds_up() -> None:
    # The matrix explains 640 as 554 callables + 86 values. If that stops being
    # true the explanation is wrong even though the total is right.
    values = len(_REGISTERED) - len(_CALLABLE)
    assert f"{len(_CALLABLE)} callables + {values} enum/type values" in _TEXT, (
        f"the matrix's split of the registry is stale; it is now "
        f"{len(_CALLABLE)} callables + {values} enum/type values"
    )


def test_every_refusal_reason_is_documented() -> None:
    for reason in REASONS:
        assert f"`{reason}`" in _TEXT, (
            f"pqtools.catalog.REASONS has a `{reason}` category that "
            "SUPPORT-MATRIX.md does not explain"
        )


def test_the_matrix_does_not_claim_mashup_engine_compatibility() -> None:
    # The one claim this package must never make.
    assert "does not claim" in _TEXT and "Mashup Engine" in _TEXT
    # Computed, not pinned as a literal: the counts test forces the
    # numerator and denominator to track the registry, but a percentage
    # written beside them could go stale on its own and still pass.
    share = 100 * len(set(DOCUMENTED) & _REGISTERED) / len(DOCUMENTED)
    assert f"{share:.1f}% of NAMES" in _TEXT, (
        f"the matrix must state {share:.1f}% and keep it attached to the word "
        "NAMES; detached, it reads as a compatibility figure"
    )


def test_the_worked_example_counts_are_the_real_ones() -> None:
    """The three numbers in the matrix that were hand-written.

    The file exists to be the machine-checked source of truth, and it
    carried 786 / 165 / 141 as prose that nothing verified - the same shape
    of claim it was created to replace. 141 also reaches README.md and
    llms.txt through `scripts/sync_builtin_list.py`.

    Evaluating the corpus here would duplicate a two-minute run, so the
    match count is read from the floor `tests/test_doc_examples.py` already
    asserts and ratchets. The other two are counted directly.
    """
    import tests.test_doc_examples as corpus

    for label, actual in (
        ("Harvested from the reference", len(corpus.CASES)),
        (
            r"\.\.\. whose printed Output is itself evaluable M",
            sum(1 for _n, _i, ex in corpus.CASES if ex["output"]),
        ),
        (
            r"\.\.\. of those, reproducing their documented value exactly",
            _doc_example_floor(),
        ),
    ):
        pattern = re.compile(r"^\|\s*" + label + r"\s*\|\s*(\d+)\s*\|", re.MULTILINE)
        found = pattern.search(_TEXT)
        assert found is not None, f"SUPPORT-MATRIX.md has no row for {label!r}"
        assert int(found.group(1)) == actual, (
            f"SUPPORT-MATRIX.md says {found.group(1)} for {label!r}; the real "
            f"number is {actual}"
        )


def _doc_example_floor() -> int:
    """The match floor test_doc_examples.py asserts, read from its source.

    Reading it keeps this file from becoming a second place the number has
    to be remembered.
    """
    source = (_ROOT / "tests" / "test_doc_examples.py").read_text(encoding="utf-8")
    match = re.search(r"assert matched >= (\d+)", source)
    assert match is not None, "test_doc_examples.py no longer asserts a floor"
    return int(match.group(1))


# --- the connector table is a claim about the code ------------------------


def _connector_rows() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    section = _TEXT.split("## Connectors", 1)[1].split("###", 1)[0]
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) != 3 or cells[0].startswith(("Source", "---")):
            continue
        for name in re.findall(r"`([A-Za-z]+\.[A-Za-z*]+)`", cells[0]):
            rows.append((name, cells[1]))
    return rows


CONNECTORS = _connector_rows()


def test_the_connector_table_was_parsed() -> None:
    assert len(CONNECTORS) > 10, (
        f"only {len(CONNECTORS)} connector rows parsed; the table's shape "
        "changed and the checks below stopped checking anything"
    )


@pytest.mark.parametrize(("name", "status"), CONNECTORS)
def test_a_connector_row_matches_the_registry(name: str, status: str) -> None:
    """A row saying "works" must name something that exists and is callable.

    This is the check that the README's stale paragraph would have failed for
    four releases: it claimed engine-backed connectors refuse, while the code
    implemented them.
    """
    if name.endswith(".*"):
        prefix = name[:-1]
        matching = [n for n in _REGISTERED if n.startswith(prefix)]
        if status.startswith("refuses"):
            assert matching == [], (
                f"{name} is listed as refusing, but these are registered: "
                f"{sorted(matching)}"
            )
        else:
            assert matching, f"{name} is listed as working but nothing matches it"
        return
    if status.startswith("refuses"):
        assert name not in _REGISTERED, (
            f"SUPPORT-MATRIX.md says {name} refuses by name, but it is "
            "registered and callable"
        )
    else:
        assert name in _CALLABLE, (
            f"SUPPORT-MATRIX.md says {name} works, but it is not a callable "
            "builtin. The table is the authoritative statement - fix whichever "
            "of the two is wrong."
        )


# --- the stale documents must stay labelled -------------------------------


@pytest.mark.parametrize(
    "relative",
    ["ops/STATUS.md", ".planning/m-inventory.md"],
)
def test_a_superseded_document_says_so_at_the_top(relative: str) -> None:
    """Both files describe earlier releases while reading as current.

    `ops/STATUS.md` documents 0.1.0 under the package's former name and dates
    itself "last verified"; `.planning/m-inventory.md` quotes a path that no
    longer exists. They are kept for provenance, so the fix is a label, not a
    deletion - and the label has to survive, or the ambiguity comes back.
    """
    path = _ROOT / relative
    if not path.exists():
        pytest.skip(f"{relative} has been removed")
    head = path.read_text(encoding="utf-8")[:1200]
    assert "HISTORICAL RECORD" in head, (
        f"{relative} describes a superseded release but no longer says so in "
        "its first lines. Restore the banner, or delete the file - what it "
        "must not do is read as current."
    )
    assert "SUPPORT-MATRIX.md" in head, (
        f"{relative}'s banner must point at the authoritative file"
    )
