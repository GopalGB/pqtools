"""The goal fixtures for pqtools 0.5.0 - see PRD-0.5.0-builtins.md.

Each scenario under ``tests/fixtures/realworld/<NN_name>/`` is a query
written the way Power Query's own UI actually emits it (not hand-simplified
M), plus the CSV(s) it needs bound and the output a human worked out by
hand. This module wires each one through :func:`pqtools.evaluate.evaluate`
exactly the way the CLI does (see ``_load_binding`` in ``cli.py``: every
``--bind NAME=path.csv`` becomes ``csv.DictReader`` over that file, and the
result is passed as ``bindings[NAME]``) and compares the result to the
worked-out expected table.

These are the ACTUAL DONE-CRITERION for 0.5.0 (PRD, "Verification" section):
"the four tests/fixtures/realworld/ queries evaluate correctly". Every test
here is ``xfail(strict=False)`` today because pqtools does not implement the
builtins these queries need yet - that is the entire point of the PRD. Each
xfail reason names the exact builtin the evaluator currently dies on and
quotes the exact exception it raises (verified by running the scenario
against the pre-0.5.0 evaluator - see the fixtures' README.md "Verified
failure" sections). When a builtin lands, its scenario keeps failing (a
*different* missing builtin surfaces next) until the whole chain is
implemented, at which point someone flips ``strict=False`` to ``True`` (or
drops the marker) as a real regression gate.

Nothing here is weakened to make it pass: the queries are the queries a
human designing "clean and type a CSV", "group and aggregate", "merge two
tables", and "unpivot wide data with dates" would get out of the Power
Query UI, and the expected output was worked out independently of pqtools
(see each fixture's README section for exactly how).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from pqtools.cli import _load_binding
from pqtools.evaluate import evaluate

_FIXTURES = Path(__file__).parent / "fixtures" / "realworld"


def _load_scenario(name: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Load a scenario's query text, resolved bindings, and expected table.

    Bindings are loaded with the CLI's own ``_load_binding`` (csv.DictReader
    under the hood) so this test exercises exactly what ``pq eval --bind
    NAME=file.csv`` would do - not a hand-rolled substitute loader.
    """
    scenario_dir = _FIXTURES / name
    query = (scenario_dir / "query.pq").read_text(encoding="utf-8")
    expected = json.loads((scenario_dir / "expected.json").read_text(encoding="utf-8"))
    bind_map = _BINDINGS[name]
    bindings = {
        var_name: _load_binding(scenario_dir / csv_name)
        for var_name, csv_name in bind_map.items()
    }
    return query, bindings, expected


_BINDINGS: dict[str, dict[str, str]] = {
    "01_clean_and_type": {"Source": "sales.csv"},
    "02_group_and_aggregate": {"Source": "transactions.csv"},
    "03_merge_two_tables": {
        "Source": "orders.csv",
        "CustomersSource": "customers.csv",
    },
    "04_unpivot_and_dates": {"Source": "category_sales.csv"},
}


# --------------------------------------------------------------------------
# Comparison - tolerant of the specific representation choices an
# implementation is free to make (float rounding, int-vs-float on whole
# numbers, a `date` object vs its ISO string) without being tolerant of
# actually wrong values.
# --------------------------------------------------------------------------


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        if not isinstance(actual, (int, float)):
            return False
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    if expected is None:
        return actual is None
    if isinstance(expected, str):
        if isinstance(actual, str):
            return actual == expected
        # A date/datetime value is allowed to come back as a real date
        # object rather than a string - compare its ISO form instead of
        # forcing one specific representation choice.
        isoformat = getattr(actual, "isoformat", None)
        if callable(isoformat):
            return str(isoformat()) == expected
        return False
    return bool(actual == expected)


def _assert_table_equal(actual: Any, expected: list[dict[str, Any]]) -> None:
    assert isinstance(actual, list), (
        f"expected a table (list of records), got {actual!r}"
    )
    assert len(actual) == len(expected), (
        f"row count mismatch: got {len(actual)}, expected {len(expected)}\n"
        f"actual={actual!r}"
    )
    for index, (actual_row, expected_row) in enumerate(
        zip(actual, expected, strict=True)
    ):
        assert isinstance(actual_row, dict), (
            f"row {index}: expected a record, got {actual_row!r}"
        )
        assert set(actual_row) == set(expected_row), (
            f"row {index}: column mismatch: "
            f"got {sorted(actual_row)}, expected {sorted(expected_row)}"
        )
        for key, expected_value in expected_row.items():
            actual_value = actual_row[key]
            assert _values_equal(actual_value, expected_value), (
                f"row {index} column {key!r}: got {actual_value!r}, "
                f"expected {expected_value!r}"
            )


# --------------------------------------------------------------------------
# Scenario 1 - clean-and-type a CSV import (P0: Table.TransformColumnTypes)
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "dies on Table.TransformColumnTypes: UnsupportedError "
        "'unknown identifier: Table.TransformColumnTypes' (not yet "
        "implemented; PRD-0.5.0-builtins.md P0). Table.Sort/AddColumn/"
        "RenameColumns/SelectRows used later in the chain are already "
        "implemented, so this is the first real gap the evaluator hits "
        "walking the let-chain back from the result."
    ),
)
def test_clean_and_type_csv_import():
    query, bindings, expected = _load_scenario("01_clean_and_type")
    result = evaluate(query, bindings=bindings)
    _assert_table_equal(result, expected)


# --------------------------------------------------------------------------
# Scenario 2 - group and aggregate (P1: Table.Group)
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "dies on Table.Group: UnsupportedError "
        "'unknown identifier: Table.Group' (not yet implemented; "
        "PRD-0.5.0-builtins.md P1). It is the query's own final step, so "
        "the evaluator hits it immediately without ever reaching the "
        "TransformColumnTypes step underneath."
    ),
)
def test_group_and_aggregate():
    query, bindings, expected = _load_scenario("02_group_and_aggregate")
    result = evaluate(query, bindings=bindings)
    _assert_table_equal(result, expected)


# --------------------------------------------------------------------------
# Scenario 3 - merge two tables (P1: Table.NestedJoin + Table.ExpandTableColumn)
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "dies on Table.ExpandTableColumn: UnsupportedError "
        "'unknown identifier: Table.ExpandTableColumn' (not yet "
        "implemented; PRD-0.5.0-builtins.md P1). This is the query's last "
        "step, so its callee is resolved (and fails) before its first "
        'argument - the #"Merged Queries" step calling the equally '
        "unimplemented Table.NestedJoin - is ever evaluated. Once "
        "Table.ExpandTableColumn lands, Table.NestedJoin becomes the next "
        "wall for this fixture, not the last one."
    ),
)
def test_merge_two_tables():
    query, bindings, expected = _load_scenario("03_merge_two_tables")
    result = evaluate(query, bindings=bindings)
    _assert_table_equal(result, expected)


# --------------------------------------------------------------------------
# Scenario 4 - unpivot wide data + dates (P1/P2: Table.UnpivotOtherColumns,
# Date.Year, Date.MonthName)
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "dies on Table.UnpivotOtherColumns: UnsupportedError "
        "'unknown identifier: Table.UnpivotOtherColumns' (not yet "
        "implemented; PRD-0.5.0-builtins.md P1). Table.AddColumn (used "
        "twice afterwards for the Year/Month Name columns) is already "
        "implemented, so the evaluator walks past both of those and dies "
        "on the unpivot step underneath - Date.Year/Date.MonthName "
        "(P2) are never even reached yet."
    ),
)
def test_unpivot_wide_data_and_dates():
    query, bindings, expected = _load_scenario("04_unpivot_and_dates")
    result = evaluate(query, bindings=bindings)
    _assert_table_equal(result, expected)
