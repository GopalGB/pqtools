"""Credentials come from the environment, and values cannot inject keywords.

Two defects, one function apart.

1. The README states twice, as a security property, that "credentials come
   from the environment, not from the M file". `_credentials` read
   `[Username=..., Password=...]` out of the record and PREFERRED them over
   the environment. Neither is a documented Power Query option - the
   connector pages list no credential options, because real Power Query uses
   its credential store - so honouring them invented two options as well.

2. Values were interpolated into the ODBC connection string raw. A password
   of `x;Encrypt=no` did not make a wrong password; it added a
   connection-string KEYWORD, and that one turns TLS off. A password that
   legitimately contained `;` or `}` could not be expressed at all.

Every credential in this file is a dummy literal written for the test.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from pqtools import EvalError, UnsupportedError, evaluate
from pqtools.io import IOPolicy

ALLOW_DB = IOPolicy(allow_db=True)

USER = "dummy-user"
PASSWORD = "dummy-password"


def _parse_connection_string(text: str) -> dict[str, str]:
    """Read a connection string the way an ODBC driver does.

    Substring assertions cannot tell `PWD={a;b}` (one value) from `PWD=a;b`
    (a value and a stray keyword), which is the entire difference this fix
    makes. So the tests assert against the keywords a driver would actually
    see.
    """
    out: dict[str, str] = {}
    i, n = 0, len(text)
    while i < n:
        equals = text.index("=", i)
        key = text[i:equals]
        i = equals + 1
        if i < n and text[i] == "{":
            i += 1
            chars: list[str] = []
            while True:
                if text[i] == "}":
                    if i + 1 < n and text[i + 1] == "}":
                        chars.append("}")
                        i += 2
                        continue
                    i += 1
                    break
                chars.append(text[i])
                i += 1
            value = "".join(chars)
            while i < n and text[i] != ";":
                i += 1
            i += 1
        else:
            end = text.find(";", i)
            if end < 0:
                end = n
            value = text[i:end]
            i = end + 1
        out[key] = value
    return out


class _Cursor:
    description = (("TABLE_SCHEMA",), ("TABLE_NAME",), ("TABLE_TYPE",))

    def execute(self, sql: str) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []

    def close(self) -> None:
        return None


class _Connection:
    timeout: Any = None

    def cursor(self) -> _Cursor:
        return _Cursor()

    def close(self) -> None:
        return None


@pytest.fixture
def odbc(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    strings: list[str] = []

    def connect(connection_string: str, **kwargs: Any) -> _Connection:
        strings.append(connection_string)
        return _Connection()

    module = types.ModuleType("pyodbc")
    module.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodbc", module)
    for name in (
        "PQTOOLS_SQL_USER",
        "PQTOOLS_SQL_PASSWORD",
        "PQTOOLS_PG_USER",
        "PQTOOLS_PG_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    return strings


# --- the environment is the only source -----------------------------------


@pytest.mark.parametrize(
    ("call", "option", "variable"),
    [
        ('Sql.Database("s", "d", [Username = "u"])', "Username", "PQTOOLS_SQL_USER"),
        (
            'Sql.Database("s", "d", [Password = "p"])',
            "Password",
            "PQTOOLS_SQL_PASSWORD",
        ),
        (
            'PostgreSQL.Database("s", "d", [Username = "u"])',
            "Username",
            "PQTOOLS_PG_USER",
        ),
        (
            'MySQL.Database("s", "d", [Password = "p"])',
            "Password",
            "PQTOOLS_MYSQL_PASSWORD",
        ),
        (
            'Oracle.Database("s", [Password = "p"])',
            "Password",
            "PQTOOLS_ORACLE_PASSWORD",
        ),
    ],
)
def test_a_credential_in_the_m_file_is_refused_and_names_its_variable(
    odbc: list[str], call: str, option: str, variable: str
) -> None:
    with pytest.raises(UnsupportedError) as caught:
        evaluate(call, io=ALLOW_DB)
    message = str(caught.value)
    assert option in message
    assert variable in message
    # It must refuse before connecting - a query carrying a password must
    # not reach a server at all.
    assert odbc == []


def test_the_environment_supplies_the_credentials(
    odbc: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PQTOOLS_SQL_USER", USER)
    monkeypatch.setenv("PQTOOLS_SQL_PASSWORD", PASSWORD)
    evaluate('Sql.Database("srv", "db")', io=ALLOW_DB)
    parsed = _parse_connection_string(odbc[0])
    assert parsed["UID"] == USER
    assert parsed["PWD"] == PASSWORD


def test_without_credentials_it_falls_back_to_trusted_connection(
    odbc: list[str],
) -> None:
    evaluate('Sql.Database("srv", "db")', io=ALLOW_DB)
    parsed = _parse_connection_string(odbc[0])
    assert parsed["Trusted_Connection"] == "yes"
    assert "UID" not in parsed


def test_the_readme_names_the_variables_the_code_actually_reads() -> None:
    # The defect was documentation and code disagreeing. This fails if they
    # drift apart again in either direction.
    import pathlib
    import re

    source = pathlib.Path("src/pqtools/builtins/_sources.py").read_text(
        encoding="utf-8"
    )
    readme = pathlib.Path("README.md").read_text(encoding="utf-8")
    prefixes = set(re.findall(r'"(PQTOOLS_[A-Z]+)"', source))
    assert prefixes, "no credential prefixes found in the connector source"
    for prefix in prefixes:
        assert f"{prefix}_USER" in readme or f"{prefix}_*" in readme, (
            f"{prefix} is read by the code but never named in the README"
        )


# --- a value cannot become a keyword --------------------------------------


def test_a_semicolon_in_a_password_stays_inside_the_password(
    odbc: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The exact injection: this used to add Encrypt=no as a real keyword and
    # silently drop the connection's encryption.
    hostile = "x;Trusted_Connection=yes;Encrypt=no"
    monkeypatch.setenv("PQTOOLS_SQL_USER", USER)
    monkeypatch.setenv("PQTOOLS_SQL_PASSWORD", hostile)
    evaluate('Sql.Database("srv", "db")', io=ALLOW_DB)
    parsed = _parse_connection_string(odbc[0])
    assert parsed["PWD"] == hostile
    assert "Encrypt" not in parsed
    assert parsed["TrustServerCertificate"] == "yes"


@pytest.mark.parametrize("field", ["server", "database"])
def test_a_semicolon_in_the_server_or_database_stays_inside_it(
    odbc: list[str], field: str
) -> None:
    hostile = "value;Encrypt=no"
    server, database = ("srv", "db")
    if field == "server":
        server = hostile
    else:
        database = hostile
    evaluate(f'Sql.Database("{server}", "{database}")', io=ALLOW_DB)
    parsed = _parse_connection_string(odbc[0])
    assert parsed["SERVER"] == server
    assert parsed["DATABASE"] == database
    assert "Encrypt" not in parsed


def test_a_password_may_legitimately_contain_a_brace(
    odbc: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Not an attack - a perfectly ordinary generated password that could not
    # previously survive the trip.
    awkward = "p}a;s{s"
    monkeypatch.setenv("PQTOOLS_SQL_USER", USER)
    monkeypatch.setenv("PQTOOLS_SQL_PASSWORD", awkward)
    evaluate('Sql.Database("srv", "db")', io=ALLOW_DB)
    assert _parse_connection_string(odbc[0])["PWD"] == awkward


def test_ordinary_values_are_not_wrapped(
    odbc: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PQTOOLS_SQL_USER", USER)
    monkeypatch.setenv("PQTOOLS_SQL_PASSWORD", PASSWORD)
    evaluate('Sql.Database("srv", "db")', io=ALLOW_DB)
    assert f"UID={USER}" in odbc[0]
    assert "{" not in odbc[0].split("TrustServerCertificate")[1]


def test_the_driver_keyword_is_not_double_quoted(odbc: list[str]) -> None:
    # DRIVER={...} is written already-quoted; escaping it again would break
    # every connection this module opens.
    evaluate('Sql.Database("srv", "db")', io=ALLOW_DB)
    assert odbc[0].startswith("DRIVER={ODBC Driver 18 for SQL Server};")
    assert _parse_connection_string(odbc[0])["DRIVER"] == (
        "ODBC Driver 18 for SQL Server"
    )


def test_the_odbc_record_form_escapes_its_values_too(odbc: list[str]) -> None:
    # Odbc.Query's `connectionString as any` record spelling built the same
    # string by the same raw interpolation.
    evaluate(
        'Odbc.Query([Driver = "d", PWD = "x;Encrypt=no"], "select 1")', io=ALLOW_DB
    )
    parsed = _parse_connection_string(odbc[0])
    assert parsed["PWD"] == "x;Encrypt=no"
    assert "Encrypt" not in parsed


def test_a_connection_property_NAME_cannot_inject_a_keyword(
    odbc: list[str],
) -> None:
    """Escaping the value was half a fix; the KEY was still raw.

    An M record field name can be any quoted identifier, so
    `Odbc.Query([#"UID=sa;Encrypt" = "no"], ...)` emitted three
    connection-string keywords through the key while its value was dutifully
    escaped. Found by the claude-opus-5 review of the escaping fix.
    """
    with pytest.raises(EvalError, match="connection-string delimiter"):
        evaluate(
            'Odbc.Query([Driver = "d", #"UID=sa;Encrypt" = "no"], "select 1")',
            io=ALLOW_DB,
        )
    assert odbc == []


def test_an_ordinary_property_name_still_works(odbc: list[str]) -> None:
    evaluate('Odbc.Query([Driver = "d", UID = "u"], "select 1")', io=ALLOW_DB)
    assert _parse_connection_string(odbc[0]) == {"Driver": "d", "UID": "u"}


def test_env_credentials_with_an_explicit_connection_string_are_refused(
    odbc: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """They were read and then silently dropped.

    `UID`/`PWD` are only appended in the branch that BUILDS the connection
    string, so with an explicit `ConnectionString` the environment
    credentials went nowhere - and the caller connected as somebody else
    without being told. `MultiSubnetFailover`, three lines away and in the
    identical position, was already refused by name.
    """
    monkeypatch.setenv("PQTOOLS_SQL_USER", USER)
    monkeypatch.setenv("PQTOOLS_SQL_PASSWORD", PASSWORD)
    with pytest.raises(UnsupportedError, match="cannot be applied"):
        evaluate('Sql.Database("s", "d", [ConnectionString = "DSN=x"])', io=ALLOW_DB)
    assert odbc == []


def test_an_explicit_connection_string_alone_still_works(odbc: list[str]) -> None:
    evaluate('Sql.Database("s", "d", [ConnectionString = "DSN=x"])', io=ALLOW_DB)
    assert odbc == ["DSN=x"]
