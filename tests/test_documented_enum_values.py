"""Every registered enum value must equal the number Microsoft documents.

`_enums.py` says a wrong number here would be "silent wrongness of the worst
kind": `Text.PositionOf(t, s, Occurrence.Last)` would quietly return the FIRST
match, with no error anywhere. Nothing checked that claim - the numbers were
transcribed by hand from pages read once.

The check was also missing the pages that mattered most. `QuoteStyle.*`,
`ExtraValues.*` and `CsvStyle.*` were registered as opaque self-naming strings
on the recorded grounds that their numbering was unconfirmed. It was not
unconfirmed, it was UNFETCHED: `quotestyle-type`, `extravalues-type` and
`csvstyle-type` are all real pages with the usual Name/Value table. The guess
cost a legal M call - `QuoteStyle.Csv` is 1, so passing the literal 1 was
refused.

Fixture: `tests/fixtures/m-enum-values.json`, from
`scripts/harvest_signatures.py`. Offline, like the signature gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pqtools.evaluate import BUILTINS

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "m-enum-values.json"
DOCUMENTED_VALUES: dict[str, int] = json.loads(_FIXTURE.read_text(encoding="utf-8"))

# Registered names that are enum-like VALUES rather than functions.
REGISTERED_VALUES = sorted(
    name
    for name, value in BUILTINS.items()
    if "." in name and not callable(value) and isinstance(value, int)
)

# Ascription type values (`Int64.Type`, `Text.Type`, ...) are _MType objects,
# not numbers, and have no Name/Value table - they are excluded by the
# isinstance check above, not by an exception list.
CHECKABLE = [name for name in REGISTERED_VALUES if name in DOCUMENTED_VALUES]


def test_the_enum_fixture_is_not_empty() -> None:
    # A harvester that stopped matching would make every check below vacuous.
    assert len(DOCUMENTED_VALUES) > 60, (
        f"only {len(DOCUMENTED_VALUES)} enum values harvested; the "
        "`*-type` page table parser has stopped working"
    )


def test_every_registered_enum_value_is_documented() -> None:
    """A registered number with no page behind it is a number someone made up."""
    undocumented = sorted(set(REGISTERED_VALUES) - set(DOCUMENTED_VALUES))
    assert undocumented == [], (
        f"{len(undocumented)} registered enum value(s) have no documented "
        f"number: {undocumented}. Either fetch the `<family>-type` page and "
        "re-run scripts/harvest_signatures.py, or do not register the name - "
        "an unknown identifier is better than a wrong number."
    )


@pytest.mark.parametrize("name", CHECKABLE)
def test_a_registered_enum_value_matches_its_page(name: str) -> None:
    assert BUILTINS[name] == DOCUMENTED_VALUES[name], (
        f"{name} is registered as {BUILTINS[name]} but its `*-type` page says "
        f"{DOCUMENTED_VALUES[name]}. A wrong enum number is silent: the call "
        "still runs and returns the wrong answer."
    )


def test_the_three_families_that_were_guessed_are_now_numbers() -> None:
    # Pinned by name because these are the ones that were wrong, and a
    # regression would put a string back where M has an integer.
    for name, expected in [
        ("QuoteStyle.None", 0),
        ("QuoteStyle.Csv", 1),
        ("ExtraValues.List", 0),
        ("ExtraValues.Error", 1),
        ("ExtraValues.Ignore", 2),
        ("CsvStyle.QuoteAfterDelimiter", 0),
        ("CsvStyle.QuoteAlways", 1),
    ]:
        assert BUILTINS[name] == expected, f"{name} should be {expected}"
