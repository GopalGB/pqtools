"""Selecting one table from SQL navigation must not read the others.

`Sql.Database(server, db)` returns one row per catalog entry with a `Data`
field, and a query then picks one:

    Source{[Schema="dbo", Item="Wanted"]}[Data]

`Data` used to be a completed `SELECT *` for EVERY table, run before the
query said which one it wanted. A mocked catalog containing `Wanted` and
`Unrelated` fetched both, so reading one small table could fail - or move a
great deal of data - because of an unrelated table beside it.

The driver is a mock: these assert the query flow, with no live database.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from pqtools import EvalError, evaluate
from pqtools.io import IOPolicy

ALLOW_DB = IOPolicy(allow_db=True)

CATALOG = [
    ("dbo", "Wanted", "BASE TABLE"),
    ("dbo", "Unrelated", "BASE TABLE"),
    ("dbo", "AlsoUnrelated", "VIEW"),
]
TABLE_ROWS = {"Wanted": [(1, "keep")], "Unrelated": [(2, "noise")], "AlsoUnrelated": []}


class _Cursor:
    def __init__(self, log: dict[str, Any]) -> None:
        self._log = log
        self.description: Any = None
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str) -> None:
        self._log["queries"].append(sql)
        if "INFORMATION_SCHEMA" in sql:
            self.description = (("TABLE_SCHEMA",), ("TABLE_NAME",), ("TABLE_TYPE",))
            self._rows = list(CATALOG)
            return
        for name, rows in TABLE_ROWS.items():
            if f'"{name}"' in sql:
                if name in self._log["fail"]:
                    raise RuntimeError(f"boom reading {name}")
                self.description = (("Id",), ("Note",))
                self._rows = list(rows)
                return
        raise AssertionError(f"unexpected query: {sql}")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None:
        self._log["cursors_closed"] += 1


class _Connection:
    def __init__(self, log: dict[str, Any]) -> None:
        self._log = log
        self.timeout: int | None = None

    def cursor(self) -> _Cursor:
        return _Cursor(self._log)

    def close(self) -> None:
        self._log["closed"] += 1


@pytest.fixture
def odbc(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    log: dict[str, Any] = {
        "queries": [],
        "opened": 0,
        "closed": 0,
        "cursors_closed": 0,
        "fail": set(),
    }

    def connect(connection_string: str, **kwargs: Any) -> _Connection:
        log["opened"] += 1
        return _Connection(log)

    module = types.ModuleType("pyodbc")
    module.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodbc", module)
    return log


def _select(item: str) -> str:
    return (
        'let S = Sql.Database("srv", "db"), '
        f'T = S{{[Schema="dbo", Item="{item}"]}}[Data] in T'
    )


def _table_reads(log: dict[str, Any]) -> list[str]:
    return [q for q in log["queries"] if "INFORMATION_SCHEMA" not in q]


# --- the defect ----------------------------------------------------------


def test_selecting_one_table_reads_only_that_table(odbc: dict[str, Any]) -> None:
    assert evaluate(_select("Wanted"), io=ALLOW_DB) == [{"Id": 1, "Note": "keep"}]
    reads = _table_reads(odbc)
    assert len(reads) == 1
    assert '"Wanted"' in reads[0]
    assert not any("Unrelated" in q for q in reads)


def test_listing_the_navigation_table_reads_no_table_at_all(
    odbc: dict[str, Any],
) -> None:
    # The catalog itself is still one query; the point is that no `SELECT *`
    # runs until a row is actually selected.
    rows = evaluate(
        'let S = Sql.Database("srv", "db") in Table.RowCount(S)', io=ALLOW_DB
    )
    assert rows == 3
    assert _table_reads(odbc) == []


def test_a_broken_unrelated_table_does_not_break_the_wanted_one(
    odbc: dict[str, Any],
) -> None:
    # This is the failure the eager version produced: an unreadable table
    # anywhere in the catalog made every navigation query fail.
    odbc["fail"].add("Unrelated")
    assert evaluate(_select("Wanted"), io=ALLOW_DB) == [{"Id": 1, "Note": "keep"}]


def test_an_error_reading_the_selected_table_still_surfaces(
    odbc: dict[str, Any],
) -> None:
    odbc["fail"].add("Wanted")
    with pytest.raises(RuntimeError, match="boom reading Wanted"):
        evaluate(_select("Wanted"), io=ALLOW_DB)


# --- connection lifecycle ------------------------------------------------


def test_every_connection_opened_is_closed(odbc: dict[str, Any]) -> None:
    evaluate(_select("Wanted"), io=ALLOW_DB)
    assert odbc["opened"] == 2  # one for the catalog, one for the selected table
    assert odbc["closed"] == odbc["opened"]


def test_an_unselected_navigation_table_holds_nothing_open(
    odbc: dict[str, Any],
) -> None:
    evaluate('let S = Sql.Database("srv", "db") in Table.ColumnNames(S)', io=ALLOW_DB)
    assert odbc["opened"] == 1
    assert odbc["closed"] == 1


def test_the_connection_is_closed_even_when_the_read_fails(
    odbc: dict[str, Any],
) -> None:
    odbc["fail"].add("Wanted")
    with pytest.raises(RuntimeError):
        evaluate(_select("Wanted"), io=ALLOW_DB)
    assert odbc["closed"] == odbc["opened"]


# --- a deferred value is never silently empty ----------------------------


def test_a_deferred_table_refuses_rather_than_looking_empty(
    odbc: dict[str, Any],
) -> None:
    # Reaching the deferred value by a path that cannot trigger the read must
    # raise, not answer "no rows". A lazy value that reported empty would be
    # exactly the silent wrong answer this package refuses to produce.
    from pqtools.builtins._shared import _DeferredRows

    deferred = _DeferredRows(lambda: [{"Id": 1}], "Sql.Database: dbo.Wanted")
    with pytest.raises(EvalError, match="read on demand"):
        list(deferred)
    with pytest.raises(EvalError, match="read on demand"):
        len(deferred)
    assert "unread" in repr(deferred)


def test_a_deferred_table_is_read_once_and_reused(odbc: dict[str, Any]) -> None:
    source = (
        'let S = Sql.Database("srv", "db"), '
        'T = S{[Schema="dbo", Item="Wanted"]}[Data] '
        "in Table.RowCount(T) + Table.RowCount(T)"
    )
    assert evaluate(source, io=ALLOW_DB) == 2
    assert len(_table_reads(odbc)) == 1


def test_the_db_gate_still_applies_before_any_connection(
    odbc: dict[str, Any],
) -> None:
    from pqtools.io import IOBlockedError

    with pytest.raises(IOBlockedError, match="--allow-db"):
        evaluate(_select("Wanted"))
    assert odbc["opened"] == 0
