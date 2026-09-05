"""Connectors that leave the machine: ``Web.Contents``, ``Sql.Database``, ...

These were refused wholesale until 0.9.0, on the reasoning that a connector
belongs to Microsoft's Mashup Engine. That reasoning was wrong, and it was
wrong twice: once for local files (fixed in 0.8.0) and again here.

``Sql.Database(server, db)`` opens a TDS connection and reads rows. Python
does that. ``Web.Contents(url)`` is an HTTP GET. Python does that too. What
the Mashup Engine actually owns is **query folding** - rewriting a chain of
``Table.SelectRows``/``Table.Group`` steps into a single remote SQL statement
so the work happens on the server. That is an optimisation, not a semantic:
an unfolded query returns the same rows, it just moves more bytes.

pandas settles this the same way. ``pd.read_sql`` does not fold either - you
pull, then you ``groupby`` in memory - and nobody calls pandas an
approximation of SQL. So:

- **Implemented** - the connection and the data. Correct rows, real types.
- **Not implemented** - folding. A query that would fold in Power BI runs
  here, it just runs locally. Where that difference is observable (a `Query`
  you did not write, row limits, server-side collation) the docstrings say so.
- **Gated** - every function here goes through :mod:`pqtools.io` first,
  because the M source names the destination, not the caller. Off unless the
  caller passes ``--allow-net`` / ``--allow-db``.

Driver dependencies are optional extras, exactly as pandas keeps psycopg and
SQLAlchemy optional. A missing driver raises a message naming the extra to
install rather than a bare ``ModuleNotFoundError``.

Owned by exactly one implementer; register new names in this module's own
``BUILTINS`` dict (see ``builtins/__init__.py``).
"""

from __future__ import annotations

import datetime as _dt
import io as _io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..io import IOBlockedError
from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _require_record,
    _require_str,
    _type_name,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx
else:  # pragma: no cover - runtime alias only
    _Ctx = Any


_USER_AGENT = "pqtools/0.9 (+https://github.com/GopalGB/pqtools)"

# Response size ceiling. A connector with no limit is a memory-exhaustion bug
# waiting for one hostile URL; 256 MiB is far above any real Power Query
# source and far below anything that takes the process down.
_MAX_RESPONSE_BYTES = 256 * 1024 * 1024


def _policy(ctx: _Ctx) -> Any:
    """The active IOPolicy. Absent only if a caller built _Ctx by hand."""
    policy = getattr(ctx, "io", None)
    if policy is None:  # pragma: no cover - defensive
        raise IOBlockedError(
            "no IOPolicy on this evaluation context; network and database "
            "connectors are unavailable"
        )
    return policy


def _optional_record(value: Any, what: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    raise EvalError(f"{what}: options must be a record, got {_type_name(value)}")


# --------------------------------------------------------------------------
# Web.Contents / Json.Document-over-HTTP / OData.Feed
# --------------------------------------------------------------------------


def _build_url(base: str, options: dict[str, Any], what: str) -> str:
    """Apply M's RelativePath and Query options to a base URL.

    These exist in M so a chain of calls can share one base and still be
    recognised as the same data source. Applying them here keeps a real
    query working unchanged; ignoring them would silently fetch the wrong URL.
    """
    url = base
    relative = options.pop("RelativePath", None)
    if relative is not None:
        url = urllib.parse.urljoin(
            url if url.endswith("/") else url + "/", _require_str(relative)
        )
    query = options.pop("Query", None)
    if query is not None:
        pairs = _require_record(query)
        extra = {str(k): str(v) for k, v in pairs.items()}
        parts = urllib.parse.urlsplit(url)
        merged = dict(urllib.parse.parse_qsl(parts.query))
        merged.update(extra)
        url = urllib.parse.urlunsplit(
            parts._replace(query=urllib.parse.urlencode(merged))
        )
    return url


def _http_fetch(url: str, options: dict[str, Any], ctx: _Ctx, what: str) -> bytes:
    policy = _policy(ctx)
    url = _build_url(url, options, what)
    policy.check_net(url, what=what)

    headers = {"User-Agent": _USER_AGENT}
    raw_headers = options.pop("Headers", None)
    if raw_headers is not None:
        for key, value in _require_record(raw_headers).items():
            headers[str(key)] = str(value)

    content = options.pop("Content", None)
    body: bytes | None = None
    if content is not None:
        body = content if isinstance(content, bytes) else str(content).encode("utf-8")

    timeout = float(options.pop("Timeout", policy.timeout) or policy.timeout)
    # IsRetry/ManualStatusHandling change error behaviour, not the bytes; a
    # silent ignore would make a 404-tolerant query look successful.
    for unsupported in ("ManualStatusHandling", "ManualCredentials", "IsRetry"):
        if unsupported in options:
            raise UnsupportedError(f"{what}: {unsupported} option")
    if options:
        raise UnsupportedError(f"{what}: option(s) {sorted(options)}")

    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data: bytes = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise EvalError(f"{what}: HTTP {error.code} from {url}") from error
    except urllib.error.URLError as error:
        raise EvalError(f"{what}: cannot reach {url}: {error.reason}") from error
    if len(data) > _MAX_RESPONSE_BYTES:
        raise EvalError(
            f"{what}: response from {url} exceeds the {_MAX_RESPONSE_BYTES}-byte limit"
        )
    return data


def _web_contents(args: list[Any], ctx: _Ctx) -> Any:
    """``Web.Contents(url, options)`` - returns binary, as M does."""
    _arity("Web.Contents", args, 1, 2)
    url = _require_str(args[0])
    options = _optional_record(args[1] if len(args) == 2 else None, "Web.Contents")
    return _http_fetch(url, options, ctx, "Web.Contents")


def _odata_feed(args: list[Any], ctx: _Ctx) -> Any:
    """``OData.Feed(url, headers, options)`` - the ``value`` array as a table.

    Only the JSON (v4) representation is read. An Atom/XML feed raises rather
    than being half-parsed, because a partially-understood feed is the shape
    of a wrong answer.
    """
    _arity("OData.Feed", args, 1, 3)
    url = _require_str(args[0])
    options: dict[str, Any] = {}
    if len(args) >= 2 and args[1] is not None:
        options["Headers"] = _require_record(args[1])
    if len(args) == 3 and args[2] is not None:
        extra = _require_record(args[2])
        unsupported = sorted(set(extra) - {"Timeout"})
        if unsupported:
            raise UnsupportedError(f"OData.Feed: option(s) {unsupported}")
        options.update(extra)
    headers = dict(options.get("Headers") or {})
    headers.setdefault("Accept", "application/json")
    options["Headers"] = headers

    raw = _http_fetch(url, options, ctx, "OData.Feed")
    text = raw.decode("utf-8-sig", errors="strict").lstrip()
    if text.startswith("<"):
        raise UnsupportedError(
            "OData.Feed: this endpoint returned Atom/XML; only the JSON "
            "representation is read. Request JSON with an Accept header."
        )
    document = json.loads(text)
    if isinstance(document, dict) and "value" in document:
        rows = document["value"]
    elif isinstance(document, list):
        rows = document
    else:
        return document
    if not isinstance(rows, list):
        raise EvalError("OData.Feed: 'value' is not an array")
    return [row if isinstance(row, dict) else {"Value": row} for row in rows]


# --------------------------------------------------------------------------
# Folder.Files / Folder.Contents
# --------------------------------------------------------------------------


def _stat_row(path: Path, root: Path) -> dict[str, Any]:
    info = path.stat()

    def when(seconds: float) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(seconds, tz=_dt.UTC)

    return {
        # Content is lazy in Power Query. Reading eagerly here would load an
        # entire tree into memory for a query that only wanted the names, so
        # the bytes are fetched by File.Contents on the path instead.
        "Content": None,
        "Name": path.name,
        "Extension": path.suffix,
        "Date accessed": when(info.st_atime),
        "Date modified": when(info.st_mtime),
        "Date created": when(info.st_ctime),
        "Attributes": {"Size": info.st_size, "Directory": path.is_dir()},
        "Folder Path": str(path.parent) + os.sep,
    }


def _folder_root(value: Any, what: str) -> Path:
    root = Path(_require_str(value)).expanduser()
    if not root.exists():
        raise EvalError(
            f"{what}: no such folder: {root}. Real queries carry the author's "
            "own paths; use --bind to substitute your own data."
        )
    if not root.is_dir():
        raise EvalError(f"{what}: not a folder: {root}")
    return root


def _folder_files(args: list[Any], ctx: _Ctx) -> Any:
    """``Folder.Files(path)`` - every file beneath ``path``, recursively."""
    _arity("Folder.Files", args, 1, 2)
    root = _folder_root(args[0], "Folder.Files")
    return [
        _stat_row(child, root) for child in sorted(root.rglob("*")) if child.is_file()
    ]


def _folder_contents(args: list[Any], ctx: _Ctx) -> Any:
    """``Folder.Contents(path)`` - the immediate children, files and folders."""
    _arity("Folder.Contents", args, 1, 2)
    root = _folder_root(args[0], "Folder.Contents")
    return [_stat_row(child, root) for child in sorted(root.iterdir())]


# --------------------------------------------------------------------------
# Excel.Workbook
# --------------------------------------------------------------------------


def workbook_nav_table(payload: bytes, use_headers: bool) -> list[dict[str, Any]]:
    """Read workbook bytes into the ``Excel.Workbook`` navigation table.

    Split out from the builtin because the CLI's ``--bind`` needs the same
    value and has no evaluation context to hand. Keeping one reader means a
    bound workbook and an evaluated one cannot disagree about what a sheet
    contains.
    """
    try:
        import openpyxl
    except ModuleNotFoundError as error:  # pragma: no cover - env dependent
        raise EvalError(
            "Excel.Workbook needs openpyxl: pip install 'pqtools[excel]'"
        ) from error

    book = openpyxl.load_workbook(_io.BytesIO(payload), data_only=True, read_only=True)
    rows: list[dict[str, Any]] = []
    for sheet in book.worksheets:
        grid = [list(r) for r in sheet.iter_rows(values_only=True)]
        if use_headers and grid:
            header = [
                str(cell) if cell is not None else f"Column{i + 1}"
                for i, cell in enumerate(grid[0])
            ]
            data = [dict(zip(header, r, strict=False)) for r in grid[1:]]
        else:
            width = max((len(r) for r in grid), default=0)
            header = [f"Column{i + 1}" for i in range(width)]
            data = [
                {name: (r[i] if i < len(r) else None) for i, name in enumerate(header)}
                for r in grid
            ]
        rows.append(
            {
                "Name": sheet.title,
                "Data": data,
                "Item": sheet.title,
                "Kind": "Sheet",
                "Hidden": sheet.sheet_state != "visible",
            }
        )
    book.close()
    return rows


def _excel_workbook(args: list[Any], ctx: _Ctx) -> Any:
    """``Excel.Workbook(binary, useHeaders, delayTypes)`` - the sheet nav table.

    Returns one row per sheet with the sheet's cells in ``Data``, which is the
    shape ``Source{[Item="Sheet1"]}[Data]`` navigates. Named tables and ranges
    are not enumerated yet; a query that navigates to one gets an error naming
    it rather than a silently empty table.
    """
    _arity("Excel.Workbook", args, 1, 3)
    payload = args[0]
    if not isinstance(payload, bytes):
        raise EvalError(
            f"Excel.Workbook: expected binary, got {_type_name(payload)}. "
            "Pass File.Contents(path) or Web.Contents(url)."
        )
    if len(args) == 3 and args[2] is not None and not bool(args[2]):
        raise UnsupportedError("Excel.Workbook: delayTypes=false")
    use_headers = bool(args[1]) if len(args) >= 2 and args[1] is not None else False
    return workbook_nav_table(payload, use_headers)


# --------------------------------------------------------------------------
# SQL databases
# --------------------------------------------------------------------------


def _rows_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    if cursor.description is None:
        return []
    names = [str(column[0]) for column in cursor.description]
    return [dict(zip(names, row, strict=False)) for row in cursor.fetchall()]


def _credentials(options: dict[str, Any], prefix: str) -> tuple[str | None, str | None]:
    """Username/password from the options record, else from the environment.

    Environment first in the docs, options second in practice: a password
    written into an M file gets committed, and this package refuses to make
    that the path of least resistance. The options are honoured because some
    real queries already carry them, not because it is a good idea.
    """
    user = options.pop("Username", None) or os.environ.get(f"{prefix}_USER")
    password = options.pop("Password", None) or os.environ.get(f"{prefix}_PASSWORD")
    return (str(user) if user else None, str(password) if password else None)


def _require_driver(module: str, extra: str, what: str) -> Any:
    try:
        return __import__(module)
    except ModuleNotFoundError as error:
        raise EvalError(
            f"{what} needs the {module} driver: pip install 'pqtools[{extra}]'"
        ) from error


def _run_query(connection: Any, query: str) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    try:
        cursor.execute(query)
        return _rows_from_cursor(cursor)
    finally:
        cursor.close()


def _sql_database(args: list[Any], ctx: _Ctx) -> Any:
    """``Sql.Database(server, database, options)`` - SQL Server.

    With ``[Query="..."]`` the statement runs and its rows come back. Without
    it, the navigation table of tables and views comes back, which is what
    ``Source{[Schema="dbo",Item="Orders"]}[Data]`` walks.

    No folding: later ``Table.SelectRows`` steps filter locally rather than
    becoming a WHERE clause. Same rows, more bytes over the wire.
    """
    _policy(ctx).check_db(what="Sql.Database")
    _arity("Sql.Database", args, 2, 3)
    server = _require_str(args[0])
    database = _require_str(args[1])
    options = _optional_record(args[2] if len(args) == 3 else None, "Sql.Database")

    query = options.pop("Query", None)
    user, password = _credentials(options, "PQTOOLS_SQL")
    connection_string = options.pop("ConnectionString", None)
    for unsupported in (
        "CommandTimeout",
        "HierarchicalNavigation",
        "MultiSubnetFailover",
    ):
        options.pop(unsupported, None)
    if options:
        raise UnsupportedError(f"Sql.Database: option(s) {sorted(options)}")

    pyodbc = _require_driver("pyodbc", "sql", "Sql.Database")
    if connection_string is None:
        parts = [
            "DRIVER={ODBC Driver 18 for SQL Server}",
            f"SERVER={server}",
            f"DATABASE={database}",
            "TrustServerCertificate=yes",
        ]
        if user:
            parts += [f"UID={user}", f"PWD={password or ''}"]
        else:
            parts.append("Trusted_Connection=yes")
        connection_string = ";".join(parts)

    connection = pyodbc.connect(str(connection_string))
    try:
        if query is not None:
            return _run_query(connection, _require_str(query))
        catalog = _run_query(
            connection,
            "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
            "FROM INFORMATION_SCHEMA.TABLES",
        )
        return [
            {
                "Schema": row["TABLE_SCHEMA"],
                "Item": row["TABLE_NAME"],
                "Name": row["TABLE_NAME"],
                "Kind": "View" if row["TABLE_TYPE"] == "VIEW" else "Table",
                "Data": _run_query(
                    connection,
                    f'SELECT * FROM "{row["TABLE_SCHEMA"]}"."{row["TABLE_NAME"]}"',
                ),
            }
            for row in catalog
        ]
    finally:
        connection.close()


def _generic_database(
    name: str, module: str, extra: str, env: str, default_port: int
) -> Any:
    """One implementation for the three DB-API connectors that differ only in driver."""

    def connector(args: list[Any], ctx: _Ctx) -> Any:
        _policy(ctx).check_db(what=name)
        _arity(name, args, 1, 3)
        server = _require_str(args[0])
        database = _require_str(args[1]) if len(args) >= 2 else ""
        options = _optional_record(args[2] if len(args) == 3 else None, name)

        query = options.pop("Query", None)
        user, password = _credentials(options, env)
        if options:
            raise UnsupportedError(f"{name}: option(s) {sorted(options)}")

        host, _, port_text = server.partition(":")
        port = int(port_text) if port_text else default_port
        driver = _require_driver(module, extra, name)
        connection = driver.connect(
            host=host, port=port, user=user, password=password, database=database
        )
        try:
            if query is None:
                raise UnsupportedError(
                    f"{name}: navigation without a Query option is not "
                    'implemented; pass [Query="SELECT ..."]'
                )
            return _run_query(connection, _require_str(query))
        finally:
            connection.close()

    connector.__name__ = f"_{name.replace('.', '_').lower()}"
    return connector


def _odbc_query(args: list[Any], ctx: _Ctx) -> Any:
    """``Odbc.Query(connectionString, query)`` - any ODBC source."""
    _policy(ctx).check_db(what="Odbc.Query")
    _arity("Odbc.Query", args, 2)
    connection_string = _require_str(args[0])
    query = _require_str(args[1])
    pyodbc = _require_driver("pyodbc", "sql", "Odbc.Query")
    connection = pyodbc.connect(connection_string)
    try:
        return _run_query(connection, query)
    finally:
        connection.close()


def _odbc_datasource(args: list[Any], ctx: _Ctx) -> Any:
    """``Odbc.DataSource(connectionString, options)`` - the table nav list."""
    _policy(ctx).check_db(what="Odbc.DataSource")
    _arity("Odbc.DataSource", args, 1, 2)
    connection_string = _require_str(args[0])
    options = _optional_record(args[1] if len(args) == 2 else None, "Odbc.DataSource")
    query = options.pop("Query", None)
    if options:
        raise UnsupportedError(f"Odbc.DataSource: option(s) {sorted(options)}")
    pyodbc = _require_driver("pyodbc", "sql", "Odbc.DataSource")
    connection = pyodbc.connect(connection_string)
    try:
        if query is not None:
            return _run_query(connection, _require_str(query))
        cursor = connection.cursor()
        try:
            return [
                {
                    "Schema": row[1],
                    "Item": row[2],
                    "Name": row[2],
                    "Kind": "Table",
                }
                for row in cursor.tables(tableType="TABLE")
            ]
        finally:
            cursor.close()
    finally:
        connection.close()


# --------------------------------------------------------------------------
# Uri.* - string handling, no IO, no gate
# --------------------------------------------------------------------------


def _uri_parts(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Uri.Parts", args, 1)
    parts = urllib.parse.urlsplit(_require_str(args[0]))
    return {
        "Scheme": parts.scheme,
        "Host": parts.hostname or "",
        "Port": parts.port if parts.port is not None else "",
        "Path": parts.path,
        "Query": dict(urllib.parse.parse_qsl(parts.query)),
        "Fragment": parts.fragment,
        "UserName": parts.username or "",
        "Password": parts.password or "",
    }


def _uri_combine(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Uri.Combine", args, 2)
    base = _require_str(args[0])
    return urllib.parse.urljoin(
        base if base.endswith("/") else base + "/", _require_str(args[1])
    )


# There is deliberately no `Uri.UnescapeDataString`. pqtools shipped one until
# 0.10.0, but Microsoft documents exactly four Uri functions - BuildQueryString,
# Combine, EscapeDataString, Parts - and that is not among them
# (learn.microsoft.com/en-us/powerquery-m/uri-unescapedatastring is a 404).
# Implementing a function M does not have is worse than a missing one: the
# query runs here and then fails in Power Query, which inverts the only promise
# this package makes. Use Uri.Parts, whose fields are already decoded.
def _uri_escape(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Uri.EscapeDataString", args, 1)
    return urllib.parse.quote(_require_str(args[0]), safe="")


def _uri_build(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Uri.BuildQueryString", args, 1)
    return urllib.parse.urlencode(
        {str(k): str(v) for k, v in _require_record(args[0]).items()}
    )


BUILTINS: dict[str, Any] = {
    "Web.Contents": _web_contents,
    "OData.Feed": _odata_feed,
    "Folder.Files": _folder_files,
    "Folder.Contents": _folder_contents,
    "Excel.Workbook": _excel_workbook,
    "Sql.Database": _sql_database,
    "PostgreSQL.Database": _generic_database(
        "PostgreSQL.Database", "psycopg", "postgres", "PQTOOLS_PG", 5432
    ),
    "MySQL.Database": _generic_database(
        "MySQL.Database", "pymysql", "mysql", "PQTOOLS_MYSQL", 3306
    ),
    "Oracle.Database": _generic_database(
        "Oracle.Database", "oracledb", "oracle", "PQTOOLS_ORACLE", 1521
    ),
    "Odbc.Query": _odbc_query,
    "Odbc.DataSource": _odbc_datasource,
    "Uri.Parts": _uri_parts,
    "Uri.Combine": _uri_combine,
    "Uri.EscapeDataString": _uri_escape,
    "Uri.BuildQueryString": _uri_build,
}
