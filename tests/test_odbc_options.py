"""Odbc.Query's documented third argument, and its `connectionString as any`.

Both halves of the Syntax block were missing: the options record could not be
passed at all, and a connection string given as "a record of property value
pairs" - which the page states in so many words - was refused by
`_require_str` before any connection was attempted.

The driver is faked, so this runs with no ODBC installation. What it pins is
the argument handling, which is where the defect was; `pyodbc.connect` itself
is a pass-through.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from pqtools import EvalError, UnsupportedError, evaluate
from pqtools.io import IOPolicy


class _Cursor:
    description = (("a", None), ("b", None))

    def __init__(self, log: dict[str, Any]) -> None:
        self._log = log

    def execute(self, query: str) -> None:
        self._log["query"] = query

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [(1, "x")]

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, log: dict[str, Any]) -> None:
        self._log = log
        self.timeout: int | None = None

    def cursor(self) -> _Cursor:
        return _Cursor(self._log)

    def close(self) -> None:
        self._log["closed"] = True


@pytest.fixture
def odbc(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    log: dict[str, Any] = {}

    def connect(connection_string: str, **kwargs: Any) -> _Connection:
        log["connection_string"] = connection_string
        log["kwargs"] = kwargs
        connection = _Connection(log)
        log["connection"] = connection
        return connection

    module = types.ModuleType("pyodbc")
    module.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodbc", module)
    return log


ALLOW_DB = IOPolicy(allow_db=True)


def test_a_record_connection_string_is_built_into_property_pairs(
    odbc: dict[str, Any],
) -> None:
    # "connectionString can be text or a record of property value pairs.
    # Property values can either be text or number." - the page, verbatim.
    rows = evaluate('Odbc.Query([dsn = "mydsn", Port = 1521], "SELECT 1")', io=ALLOW_DB)
    assert rows == [{"a": 1, "b": "x"}]
    assert odbc["connection_string"] == "dsn=mydsn;Port=1521"


def test_a_text_connection_string_still_works(odbc: dict[str, Any]) -> None:
    evaluate('Odbc.Query("dsn=mydsn", "SELECT 1")', io=ALLOW_DB)
    assert odbc["connection_string"] == "dsn=mydsn"


def test_a_non_scalar_connection_property_is_named(odbc: dict[str, Any]) -> None:
    with pytest.raises(EvalError, match="must be text or a number"):
        evaluate('Odbc.Query([dsn = {1}], "SELECT 1")', io=ALLOW_DB)


def test_the_timeouts_are_durations_not_numbers(odbc: dict[str, Any]) -> None:
    # "ConnectionTimeout: A duration that controls how long to wait before
    # abandoning an attempt to make a connection to the server."
    evaluate(
        'Odbc.Query("dsn=x", "SELECT 1", [ConnectionTimeout = #duration(0,0,0,30), '
        "CommandTimeout = #duration(0,0,5,0)])",
        io=ALLOW_DB,
    )
    assert odbc["kwargs"] == {"timeout": 30}
    assert odbc["connection"].timeout == 300


def test_omitting_the_options_record_leaves_the_driver_defaults(
    odbc: dict[str, Any],
) -> None:
    evaluate('Odbc.Query("dsn=x", "SELECT 1")', io=ALLOW_DB)
    assert odbc["kwargs"] == {}
    assert odbc["connection"].timeout is None


def test_an_undocumented_option_is_named_rather_than_ignored(
    odbc: dict[str, Any],
) -> None:
    with pytest.raises(UnsupportedError, match="Nope"):
        evaluate('Odbc.Query("dsn=x", "SELECT 1", [Nope = 1])', io=ALLOW_DB)


def test_sql_compatible_windows_auth_true_is_the_documented_default(
    odbc: dict[str, Any],
) -> None:
    evaluate(
        'Odbc.Query("dsn=x", "SELECT 1", [SqlCompatibleWindowsAuth = true])',
        io=ALLOW_DB,
    )
    assert odbc["connection_string"] == "dsn=x"


def test_sql_compatible_windows_auth_false_is_refused_not_ignored(
    odbc: dict[str, Any],
) -> None:
    with pytest.raises(UnsupportedError, match="SqlCompatibleWindowsAuth"):
        evaluate(
            'Odbc.Query("dsn=x", "SELECT 1", [SqlCompatibleWindowsAuth = false])',
            io=ALLOW_DB,
        )


def test_odbc_datasource_takes_the_same_connection_string_shapes(
    odbc: dict[str, Any],
) -> None:
    evaluate('Odbc.DataSource([dsn = "mydsn"], [Query = "SELECT 1"])', io=ALLOW_DB)
    assert odbc["connection_string"] == "dsn=mydsn"


def test_the_db_gate_still_applies() -> None:
    from pqtools.io import IOBlockedError

    with pytest.raises(IOBlockedError, match="--allow-db"):
        evaluate('Odbc.Query("dsn=x", "SELECT 1")')
