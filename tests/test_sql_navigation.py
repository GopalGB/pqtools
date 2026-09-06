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
from pqtools.core import MQueryError
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


# --------------------------------------------------------------------------
# From the claude-opus-5 review of the deferral itself. Making the value lazy
# moved the problem to whoever prints it.
# --------------------------------------------------------------------------


def _cli(argv: list[str], tmp_path: Any, source: str) -> tuple[int, str, str]:
    import contextlib
    import io as _io

    from pqtools.cli import main

    query = tmp_path / "nav.pq"
    query.write_text(source, encoding="utf-8")
    out, err = _io.StringIO(), _io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([*argv, str(query)])
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("fmt", [[], ["--format", "csv"]])
def test_printing_an_unread_navigation_table_refuses_in_the_typed_way(
    odbc: dict[str, Any], tmp_path: Any, fmt: list[str]
) -> None:
    """`pq eval 'Sql.Database(...)'` used to end in a Python traceback.

    `_print`'s json fallback called `.as_dict()` on the deferred value and
    got `AttributeError` - a bare traceback out of the CLI, which is the
    failure shape this package exists to prevent. `--format csv` was worse:
    `csv.DictWriter` would `str()` the placeholder into a data cell, so a
    table nobody read printed as though it were a measured value.

    Forcing it here would be wrong too - that is the read-every-table
    behaviour the deferral removed. So it refuses and names the next step.
    """
    code, out, err = _cli(
        ["eval", "--allow-db", *fmt], tmp_path, 'Sql.Database("srv", "db")'
    )
    assert code != 0
    assert "Traceback" not in err and "AttributeError" not in err
    assert "has not been read" in err
    assert "Item=" in err or "Item =" in err
    # Nothing that looks like data was printed.
    assert "deferred" not in out
    # And no table was read to produce the refusal.
    assert _table_reads(odbc) == []


def test_selecting_an_item_through_the_cli_still_prints_its_rows(
    odbc: dict[str, Any], tmp_path: Any
) -> None:
    # The refusal must be specific to the unread field, not to the connector.
    code, out, err = _cli(
        ["eval", "--allow-db"],
        tmp_path,
        'let S = Sql.Database("srv", "db"), '
        'T = S{[Schema="dbo", Item="Wanted"]}[Data] in T',
    )
    assert code == 0, err
    assert '"Note": "keep"' in out


@pytest.mark.parametrize(
    "shape",
    [
        {"Data": "TOP"},
        {"nav": [{"Item": "Orders", "Data": "TOP"}]},
        {"grouped": {"inner": {"Data": "TOP"}}},
        {"deep": [[{"Data": "TOP"}]]},
    ],
)
def test_csv_refuses_an_unread_table_at_any_depth(shape: dict[str, Any]) -> None:
    """The first version of this guard looked only at top-level cells.

    `Table.Group(Sql.Database(...), {"Schema"}, {{"all", each _}})` nests the
    navigation rows inside a cell, so the deferred value went straight to
    `csv.DictWriter`, which `str()`s it into `<deferred ...>` - a table
    nobody read, printed where a value somebody measured would go. The
    comment above the check claimed that case was closed while the code did
    not do it. The JSON path never had the bug, because `json.dumps` recurses.
    """
    import json as _json

    from pqtools.builtins._shared import _DeferredRows
    from pqtools.cli import _print_csv

    deferred = _DeferredRows(lambda: [{"a": 1}], "Sql.Database: dbo.Orders")
    row = _json.loads(_json.dumps(shape).replace('"TOP"', "null"))

    def place(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: (deferred if v is None else place(v)) for k, v in node.items()}
        if isinstance(node, list):
            return [place(item) for item in node]
        return node

    with pytest.raises(MQueryError, match="has not been read"):
        _print_csv([place(row)])


def test_csv_still_prints_ordinary_rows() -> None:
    # The recursive check must not refuse data that is merely nested.
    from pqtools.cli import _print_csv

    _print_csv([{"a": 1, "b": {"nested": [1, 2]}}])


def test_a_library_caller_has_a_public_way_to_read_a_deferred_table(
    odbc: dict[str, Any],
) -> None:
    """`evaluate()` is public, so the deferred value reaches library callers.

    It raises on `len`, `iter`, `==` and `bool` by design. Without a public
    accessor that design is a dead end for anyone not using the CLI.
    """
    from pqtools import DeferredTable

    rows = evaluate('Sql.Database("srv", "db")', io=ALLOW_DB)
    data = next(r["Data"] for r in rows if r["Item"] == "Wanted")
    assert isinstance(data, DeferredTable)
    assert data.read() == [{"Id": 1, "Note": "keep"}]
    # Reading twice runs the query once.
    assert data.read() == [{"Id": 1, "Note": "keep"}]
    assert len(_table_reads(odbc)) == 1


# --- identifiers come from the catalog, and the catalog is not trusted ----


def test_a_table_name_containing_a_quote_cannot_break_out_of_its_identifier(
    odbc: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The navigation SELECT wrapped catalog names in quotes without escaping.

    A name is chosen by whoever can CREATE TABLE in that database, which is
    not always the account pqtools connects with. A name carrying a `"`
    closed the identifier early: `Wanted"."Other` read a different table
    than the one the query named, silently, and a name carrying `;` ran a
    second statement under the connecting account's rights. Found by ruff's
    bandit rule S608; present since before the audit baseline.
    """
    hostile = 'Wanted"."Other'
    monkeypatch.setattr(
        sys.modules[__name__], "CATALOG", [("dbo", hostile, "BASE TABLE")]
    )
    in_m = hostile.replace('"', '""')  # M doubles a quote inside a string literal
    evaluate(
        'let S = Sql.Database("srv", "db"), '
        f'T = S{{[Schema="dbo", Item="{in_m}"]}}[Data] in T',
        io=ALLOW_DB,
    )
    [sql] = _table_reads(odbc)
    # the quote is doubled INSIDE the identifier, so the name stays one name
    assert sql == 'SELECT * FROM "dbo"."Wanted"".""Other"'


def test_a_nul_in_one_catalog_name_does_not_break_a_healthy_table(
    odbc: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The NUL refusal must be scoped to the row that owns the bad name.

    Built eagerly in the navigation comprehension, one poisoned catalog name
    aborted `Sql.Database(...)` itself, so no table could be selected - the
    same all-or-nothing coupling
    `test_a_broken_unrelated_table_does_not_break_the_wanted_one` forbids for
    an unreadable table.
    """
    monkeypatch.setattr(
        sys.modules[__name__],
        "CATALOG",
        [("dbo", "Wanted", "BASE TABLE"), ("dbo", "bad\x00name", "BASE TABLE")],
    )
    assert evaluate(_select("Wanted"), io=ALLOW_DB) == [{"Id": 1, "Note": "keep"}]


def test_selecting_the_nul_named_table_refuses_by_name(
    odbc: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys.modules[__name__], "CATALOG", [("dbo", "bad\x00name", "BASE TABLE")]
    )
    with pytest.raises(EvalError, match="Sql.Database.*NUL"):
        evaluate(_select("bad\x00name"), io=ALLOW_DB)


def test_a_semicolon_in_a_table_name_stays_inside_one_identifier(
    odbc: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `;` case the helper's docstring claims to close.

    A second statement smuggled through a catalog name would run under the
    rights of the account pqtools connects with.
    """
    hostile = 'Wanted"; SELECT 1 --'
    monkeypatch.setattr(
        sys.modules[__name__], "CATALOG", [("dbo", hostile, "BASE TABLE")]
    )
    in_m = hostile.replace('"', '""')
    evaluate(
        'let S = Sql.Database("srv", "db"), '
        f'T = S{{[Schema="dbo", Item="{in_m}"]}}[Data] in T',
        io=ALLOW_DB,
    )
    [sql] = _table_reads(odbc)
    assert sql == 'SELECT * FROM "dbo"."Wanted""; SELECT 1 --"'
