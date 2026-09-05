"""`Sql.Database` must honour or refuse its options, never discard them.

Three documented options - CommandTimeout, HierarchicalNavigation and
MultiSubnetFailover - were popped off the record and thrown away. Each
discard is a wrong answer the caller cannot see: a query asking to be
bounded at 60 seconds ran unbounded, a query asking for the
MultiSubnetFailover connection-string property got a string without it, and
a query asking for the schema-grouped navigation table got the flat one.

`Odbc.Query`, in the same file, had validated and applied the very same
`CommandTimeout` the whole time, so the two connector families disagreed
about one documented option.

The driver is a mock throughout; nothing here needs a live database.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from pqtools import EvalError, UnsupportedError, evaluate
from pqtools.io import IOPolicy

ALLOW_DB = IOPolicy(allow_db=True)

CATALOG = [("dbo", "Orders", "BASE TABLE")]


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection
        self.description: Any = None
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str) -> None:
        self._connection.log["queries"].append((sql, self._connection))
        if "INFORMATION_SCHEMA" in sql:
            self.description = (("TABLE_SCHEMA",), ("TABLE_NAME",), ("TABLE_TYPE",))
            self._rows = list(CATALOG)
        else:
            self.description = (("Id",),)
            self._rows = [(1,)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None:
        return None


class _Connection:
    def __init__(self, log: dict[str, Any], connect_kwargs: dict[str, Any]) -> None:
        self.log = log
        self.connect_kwargs = connect_kwargs
        self._timeout: Any = "<never set>"

    @property
    def timeout(self) -> Any:
        return self._timeout

    @timeout.setter
    def timeout(self, value: Any) -> None:
        self._timeout = value

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        return None


@pytest.fixture
def odbc(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    log: dict[str, Any] = {"connections": [], "strings": [], "queries": []}

    def connect(connection_string: str, **kwargs: Any) -> _Connection:
        log["strings"].append(connection_string)
        connection = _Connection(log, kwargs)
        log["connections"].append(connection)
        return connection

    module = types.ModuleType("pyodbc")
    module.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodbc", module)
    return log


def _run(options: str, tail: str = "") -> Any:
    body = f'Sql.Database("srv", "db", [{options}])'
    return evaluate(f"let S = {body}{tail} in S", io=ALLOW_DB)


def _select_orders(options: str) -> Any:
    return evaluate(
        f'let S = Sql.Database("srv", "db", [{options}]), '
        'T = S{[Schema="dbo", Item="Orders"]}[Data] in T',
        io=ALLOW_DB,
    )


# --- CommandTimeout: was discarded, now validated and applied -------------


def test_command_timeout_is_applied_to_the_connection(odbc: dict[str, Any]) -> None:
    _run("CommandTimeout = #duration(0, 0, 1, 0)")
    assert [c.timeout for c in odbc["connections"]] == [60]


def test_command_timeout_reaches_the_deferred_read(odbc: dict[str, Any]) -> None:
    # The catalog connection is not where the caller's data is read. A
    # timeout that stopped at the catalog would bound the one query that was
    # never the slow one.
    _select_orders("CommandTimeout = #duration(0, 0, 1, 0)")
    assert len(odbc["connections"]) == 2
    assert [c.timeout for c in odbc["connections"]] == [60, 60]


def test_command_timeout_absent_leaves_the_connection_untouched(
    odbc: dict[str, Any],
) -> None:
    _run('Query = "select 1"')
    assert odbc["connections"][0].timeout == "<never set>"


def test_a_command_timeout_that_is_not_a_duration_is_refused(
    odbc: dict[str, Any],
) -> None:
    # This exact call was accepted before, and silently ran unbounded.
    with pytest.raises(EvalError, match="CommandTimeout must be a duration"):
        _run('CommandTimeout = "soon"')
    assert odbc["connections"] == []


def test_a_negative_command_timeout_is_refused(odbc: dict[str, Any]) -> None:
    with pytest.raises(EvalError, match="CommandTimeout must be positive"):
        _run("CommandTimeout = #duration(0, 0, 0, -1)")


# --- ConnectionTimeout: refused before, and Odbc.* already honoured it ----


def test_connection_timeout_is_passed_to_the_driver(odbc: dict[str, Any]) -> None:
    _run("ConnectionTimeout = #duration(0, 0, 0, 5)")
    assert odbc["connections"][0].connect_kwargs == {"timeout": 5}


def test_connection_timeout_reaches_the_deferred_read(odbc: dict[str, Any]) -> None:
    _select_orders("ConnectionTimeout = #duration(0, 0, 0, 5)")
    assert [c.connect_kwargs for c in odbc["connections"]] == [
        {"timeout": 5},
        {"timeout": 5},
    ]


def test_a_connection_timeout_that_is_not_a_duration_is_refused(
    odbc: dict[str, Any],
) -> None:
    with pytest.raises(EvalError, match="ConnectionTimeout must be a duration"):
        _run('ConnectionTimeout = "soon"')


# --- MultiSubnetFailover: was discarded, now shapes the string ------------


def test_multi_subnet_failover_sets_both_documented_properties(
    odbc: dict[str, Any],
) -> None:
    # "sets the value of the 'MultiSubnetFailover' property in the connection
    # string ... It also sets ApplicationIntent=readonly" - sql-database.
    # The second half is the half an implementation forgets.
    _run("MultiSubnetFailover = true")
    built = odbc["strings"][0]
    assert "MultiSubnetFailover=Yes" in built
    assert "ApplicationIntent=ReadOnly" in built


def test_multi_subnet_failover_false_adds_nothing(odbc: dict[str, Any]) -> None:
    _run("MultiSubnetFailover = false")
    built = odbc["strings"][0]
    assert "MultiSubnetFailover" not in built
    assert "ApplicationIntent" not in built


def test_multi_subnet_failover_must_be_a_logical(odbc: dict[str, Any]) -> None:
    with pytest.raises(EvalError, match="MultiSubnetFailover must be a logical"):
        _run('MultiSubnetFailover = "yes"')


def test_multi_subnet_failover_with_an_explicit_connection_string_is_refused(
    odbc: dict[str, Any],
) -> None:
    # The option's only effect is on a connection string this build did not
    # write. Appending to the caller's string could contradict what they set.
    with pytest.raises(UnsupportedError, match="cannot be combined"):
        _run('ConnectionString = "DSN=x", MultiSubnetFailover = true')
    assert odbc["connections"] == []


# --- HierarchicalNavigation: was discarded, now honoured or named ---------


def test_hierarchical_navigation_false_is_the_shape_this_build_returns(
    odbc: dict[str, Any],
) -> None:
    # false IS the documented default, so accepting it is not a concession.
    rows = _run("HierarchicalNavigation = false")
    assert sorted(rows[0]) == ["Data", "Item", "Kind", "Name", "Schema"]


def test_hierarchical_navigation_true_is_refused_by_name(
    odbc: dict[str, Any],
) -> None:
    # Before, this returned the FLAT table with no error - the caller asked
    # for grouping and was told nothing.
    with pytest.raises(UnsupportedError, match="HierarchicalNavigation"):
        _run("HierarchicalNavigation = true")
    assert odbc["connections"] == []


def test_hierarchical_navigation_must_be_a_logical(odbc: dict[str, Any]) -> None:
    with pytest.raises(EvalError, match="HierarchicalNavigation must be a logical"):
        _run('HierarchicalNavigation = "grouped"')


# --- the options this build does not implement are still named ------------


@pytest.mark.parametrize(
    "option",
    [
        "CreateNavigationProperties = true",
        "MaxDegreeOfParallelism = 4",
        "UnsafeTypeConversions = true",
        "OmitSRID = true",
        "EnableCrossDatabaseFolding = true",
    ],
)
def test_the_remaining_documented_options_are_refused_by_name(
    odbc: dict[str, Any], option: str
) -> None:
    name = option.split(" =")[0]
    with pytest.raises(UnsupportedError, match=name):
        _run(option)
    assert odbc["connections"] == []


# --- the other connector families: refuse, never discard ------------------


@pytest.mark.parametrize(
    "call",
    [
        'PostgreSQL.Database("srv", "db", [CommandTimeout = #duration(0,0,1,0)])',
        'MySQL.Database("srv", "db", [CommandTimeout = #duration(0,0,1,0)])',
        'Oracle.Database("srv", [CommandTimeout = #duration(0,0,1,0)])',
        'PostgreSQL.Database("srv", "db", [HierarchicalNavigation = true])',
        'MySQL.Database("srv", "db", [HierarchicalNavigation = true])',
        'Oracle.Database("srv", [HierarchicalNavigation = true])',
    ],
)
def test_the_other_families_name_the_option_they_cannot_honour(call: str) -> None:
    # These three do not implement these options. That is allowed; discarding
    # them silently is not. The refusal must arrive before any driver import,
    # so this holds whether or not the driver is installed.
    with pytest.raises(UnsupportedError) as caught:
        evaluate(call, io=ALLOW_DB)
    assert "option(s)" in str(caught.value)


# --------------------------------------------------------------------------
# From the claude-opus-5 review of the options fix. Both are the same defect
# class the fix was written to remove, one layer down.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("option", ["CommandTimeout", "ConnectionTimeout"])
def test_a_sub_second_timeout_does_not_become_no_timeout(
    odbc: dict[str, Any], option: str
) -> None:
    """`int(0.5)` is 0, and pyodbc reads 0 as NO timeout.

    So a caller asking for a TIGHTER bound than one second got no bound at
    all - silently, and in the opposite direction from what they asked for.
    Rounding up is the only direction that cannot turn a limit into its
    absence.
    """
    _run(f"{option} = #duration(0, 0, 0, 0.5)")
    connection = odbc["connections"][0]
    observed = (
        connection.timeout
        if option == "CommandTimeout"
        else connection.connect_kwargs.get("timeout")
    )
    assert observed == 1, (
        f"a half-second {option} became {observed!r}; 0 means no timeout"
    )


def test_a_whole_second_timeout_is_unchanged(odbc: dict[str, Any]) -> None:
    # Rounding up must not inflate an ordinary value.
    _run("CommandTimeout = #duration(0, 0, 1, 30)")
    assert odbc["connections"][0].timeout == 90
