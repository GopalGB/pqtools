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
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..io import IOBlockedError, IOPolicy
from ._shared import (
    EvalError,
    UnsupportedError,
    _arity,
    _DeferredRows,
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


class _PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Put every redirect hop back through the policy before following it.

    ``urlopen`` follows redirects itself, and only the URL the caller wrote
    had been checked. So a host that passed ``--allow-host``, or any public
    host at all, could answer 302 and send the fetch to ``127.0.0.1``, an
    RFC1918 service, or ``169.254.169.254`` - the cloud metadata endpoint
    :meth:`IOPolicy._reject_internal` exists to keep a stranger's workbook
    away from. The first leg being clean says nothing about the second.

    Proven before it was fixed: with ``hosts={"localhost"}`` a direct fetch
    of the redirect target was refused and the identical bytes came back
    through one hop. ``tests/test_sources_policy.py`` keeps that pair - the
    refusal is the control, without which the test could pass for the wrong
    reason.

    ``IOBlockedError`` derives from ``MQueryError``, not ``URLError``, so it
    travels out of ``urlopen`` intact instead of being reported as a
    transport failure.

    A redirect that CROSSES ORIGINS also drops the caller's credential
    headers. Allowing the hop and forwarding the token are two different
    decisions: the policy answers "may this request be made", not "may this
    server be told the caller's secret". The destination is chosen by the
    server answering 302, so a host that is permitted to be fetched is still
    not a host the caller decided to authenticate to. Verified before it was
    fixed: a feed answering 302 to a second local origin received
    ``Authorization`` in full.
    """

    #: Never forwarded across an origin boundary, whoever set them.
    _SENSITIVE = frozenset(
        {"authorization", "proxy-authorization", "cookie", "www-authenticate"}
    )

    def __init__(
        self, policy: IOPolicy, what: str, caller_headers: frozenset[str] = frozenset()
    ) -> None:
        self._policy = policy
        self._what = what
        # Every header the QUERY supplied. A credential does not have to be
        # called Authorization - `X-API-Key` is just as much a secret and
        # would not match a fixed list - so anything the caller set is
        # treated as theirs to give, not ours to forward.
        self._caller_headers = caller_headers

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        self._policy.check_net(newurl, what=f"{self._what} (redirected to)")
        following = super().redirect_request(req, fp, code, msg, headers, newurl)
        if following is None or _origin(req.full_url) == _origin(newurl):
            return following
        drop = self._SENSITIVE | self._caller_headers
        for store in (following.headers, following.unredirected_hdrs):
            for name in [k for k in store if k.lower() in drop]:
                del store[name]
        return following


def _request_timeout(
    raw: Any, default: float, what: str, option: str = "Timeout"
) -> float:
    """A documented timeout option, which is a DURATION.

    "Specifying this value as a duration will change the timeout for an HTTP
    request" - web-contents, verbatim. `float()` on a `timedelta` raises
    `TypeError`, so the documented spelling
    ``[Timeout = #duration(0, 0, 0, 30)]`` left the CLI printing a Python
    traceback instead of making the request.
    """
    if raw is None:
        return default
    if isinstance(raw, _dt.timedelta):
        seconds = raw.total_seconds()
    elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
        # A number is accepted because it is unambiguous and cheap to allow;
        # anything else is named rather than coerced.
        raise EvalError(f"{what}: {option} must be a duration, got {_type_name(raw)}")
    else:
        seconds = float(raw)
    if seconds <= 0:
        raise EvalError(f"{what}: {option} must be positive, got {seconds}")
    return seconds


def _http_fetch(url: str, options: dict[str, Any], ctx: _Ctx, what: str) -> bytes:
    """The bytes. Callers that must resolve a relative link want
    `_http_fetch_resolved`, which also reports where the response came from."""
    return _http_fetch_resolved(url, options, ctx, what)[0]


def _http_fetch_resolved(
    url: str, options: dict[str, Any], ctx: _Ctx, what: str
) -> tuple[bytes, str]:
    policy = _policy(ctx)
    url = _build_url(url, options, what)
    policy.check_net(url, what=what)

    headers = {"User-Agent": _USER_AGENT}
    caller_headers: set[str] = set()
    raw_headers = options.pop("Headers", None)
    if raw_headers is not None:
        for key, value in _require_record(raw_headers).items():
            headers[str(key)] = str(value)
            caller_headers.add(str(key).lower())
    # Accept is routinely set by the caller and carries nothing secret;
    # keeping it means a cross-origin hop still asks for the right format.
    caller_headers.discard("accept")

    content = options.pop("Content", None)
    body: bytes | None = None
    if content is not None:
        body = content if isinstance(content, bytes) else str(content).encode("utf-8")

    timeout = _request_timeout(options.pop("Timeout", None), policy.timeout, what)
    # IsRetry/ManualStatusHandling change error behaviour, not the bytes; a
    # silent ignore would make a 404-tolerant query look successful.
    for unsupported in ("ManualStatusHandling", "ManualCredentials", "IsRetry"):
        if unsupported in options:
            raise UnsupportedError(f"{what}: {unsupported} option")
    if options:
        raise UnsupportedError(f"{what}: option(s) {sorted(options)}")

    request = urllib.request.Request(url, data=body, headers=headers)
    # A private opener, not the module-level default: the handler carries
    # this evaluation's own policy, and installing it globally would leak
    # one call's permissions into the next.
    opener = urllib.request.build_opener(
        _PolicyRedirectHandler(policy, what, frozenset(caller_headers))
    )
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            data: bytes = response.read(_MAX_RESPONSE_BYTES + 1)
            # Where the bytes actually came from. A relative `@odata.nextLink`
            # has to resolve against THIS, not against the URL that was
            # requested - a page reached by a 302 to a different path would
            # otherwise produce a next URL built on the old one.
            final_url: str = response.geturl() or url
    except urllib.error.HTTPError as error:
        raise EvalError(f"{what}: HTTP {error.code} from {url}") from error
    except urllib.error.URLError as error:
        raise EvalError(f"{what}: cannot reach {url}: {error.reason}") from error
    if len(data) > _MAX_RESPONSE_BYTES:
        raise EvalError(
            f"{what}: response from {url} exceeds the {_MAX_RESPONSE_BYTES}-byte limit"
        )
    return data, final_url


def _web_contents(args: list[Any], ctx: _Ctx) -> Any:
    """``Web.Contents(url, options)`` - returns binary, as M does."""
    _arity("Web.Contents", args, 1, 2)
    url = _require_str(args[0])
    options = _optional_record(args[1] if len(args) == 2 else None, "Web.Contents")
    return _http_fetch(url, options, ctx, "Web.Contents")


# A server decides how many pages a feed has, so the ceiling is ours, not
# theirs. 200 pages of the documented 256 MB-per-response limit is already
# far past any query a person is waiting on.
_MAX_ODATA_PAGES = 200


def _odata_document(raw: bytes, url: str) -> Any:
    """Decode and parse one OData response, with typed errors throughout.

    A 200 does not promise well-formed JSON. Letting UnicodeDecodeError or
    JSONDecodeError out raises a bare Python exception through the evaluator,
    which the CLI prints as a traceback - the one shape `MQueryError` exists
    to prevent. Both become an EvalError naming the endpoint, so the reader
    learns which URL lied about its content type.
    """
    try:
        text = raw.decode("utf-8-sig", errors="strict").lstrip()
    except UnicodeDecodeError as error:
        raise EvalError(
            f"OData.Feed: {url} returned bytes that are not valid UTF-8 "
            f"({error.reason} at byte {error.start})"
        ) from error
    if text.startswith("<"):
        raise UnsupportedError(
            "OData.Feed: this endpoint returned Atom/XML; only the JSON "
            "representation is read. Request JSON with an Accept header."
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise EvalError(
            f"OData.Feed: {url} did not return valid JSON ({error.msg} at "
            f"line {error.lineno} column {error.colno})"
        ) from error


# Distinct from `_DEFAULT_PORTS` further down, which serves Uri.Parts and
# holds ints. Two module-level names that differ only in what they mean is
# how a shadowed constant silently changes behaviour.
_ORIGIN_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _origin(url: str) -> tuple[str, str]:
    """(scheme, host:port), lowercased - what "the same site" means for headers.

    The default port is normalised away. `https://host/a` and
    `https://host:443/a` are one origin, and proxies routinely spell the
    port; treating them as different would strip the caller's credentials
    from a hop that never left the service.
    """
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    default = _ORIGIN_DEFAULT_PORTS.get(scheme)
    if default is not None and netloc.endswith(f":{default}"):
        netloc = netloc[: -len(default) - 1]
    return (scheme, netloc)


def _odata_next_link(document: Any, current: str) -> str | None:
    """The absolute URL of the next page, or None when this was the last.

    v4 spells it `@odata.nextLink`; v3 minimal-metadata spells it
    `odata.nextLink`. Both may be RELATIVE to the page that carried them,
    which is why this resolves rather than using the value as given.
    """
    if not isinstance(document, dict):
        return None
    for key in ("@odata.nextLink", "odata.nextLink"):
        if key not in document:
            continue
        link = document[key]
        if not isinstance(link, str) or not link.strip():
            # Refusing beats returning page one as if it were the whole feed.
            raise EvalError(
                f"OData.Feed: {current} carried a {key} that is not a usable "
                f"URL ({link!r}), so the feed cannot be read completely"
            )
        return urllib.parse.urljoin(current, link.strip())
    return None


def _odata_feed(args: list[Any], ctx: _Ctx) -> Any:
    """``OData.Feed(url, headers, options)`` - the whole feed, as a table.

    Only the JSON (v4) representation is read. An Atom/XML feed raises rather
    than being half-parsed, because a partially-understood feed is the shape
    of a wrong answer.

    Server-driven paging is FOLLOWED. It used to be ignored: one fetch, the
    first page's `value` returned, and `@odata.nextLink` dropped - so a
    two-page feed silently produced half its rows, which is the same shape of
    wrong answer the Atom refusal above exists to avoid. Every page goes back
    through `_http_fetch`, so the network policy is re-checked per request and
    per redirect rather than once for the first URL.
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

    rows: list[Any] = []
    seen: set[str] = set()
    # `seen` detects cycles and, since it records the landed URL of a
    # redirected page too, it is not a page count. This is.
    pages = 0
    total_bytes = 0
    current = url
    origin = _origin(url)
    # Page one carries the caller's options; later pages get an ABSOLUTE URL
    # from the server, so RelativePath/Query must not be applied again -
    # only the headers and the timeout travel on, and the headers only while
    # the origin has not changed (see below).
    page_options = dict(options)
    for _ in range(_MAX_ODATA_PAGES):
        if current in seen:
            raise EvalError(
                f"OData.Feed: {current} was served again as its own next "
                "page; the feed's paging links form a cycle"
            )
        seen.add(current)
        pages += 1
        ctx.budget.tick()
        raw, landed = _http_fetch_resolved(
            current, dict(page_options), ctx, "OData.Feed"
        )
        # A redirect means the page was served from `landed`, not `current`.
        # Recording only the requested URL let a next link that named the
        # landed one through for a second, identical fetch before the cycle
        # check caught it. Bounded either way; one page late is still late.
        seen.add(landed)

        # `_http_fetch` caps ONE response. Following up to _MAX_ODATA_PAGES of
        # them turned that into a per-page cap, so the total a feed could hand
        # back was the cap times the page ceiling. Before paging existed the
        # whole feed was one response and genuinely bounded; this keeps that
        # true by bounding the sum.
        total_bytes += len(raw)
        if total_bytes > _MAX_RESPONSE_BYTES:
            raise EvalError(
                f"OData.Feed: {url} has returned more than "
                f"{_MAX_RESPONSE_BYTES} bytes across {pages} page(s); "
                "reading the rest would be unbounded, so this refuses rather "
                "than filling memory"
            )
        document = _odata_document(raw, current)

        if isinstance(document, dict) and "value" in document:
            page = document["value"]
        elif isinstance(document, list):
            page = document
        elif rows or pages > 1:
            raise EvalError(
                f"OData.Feed: {current} was reached as a next page but is not "
                "a collection response, so the feed cannot be read completely"
            )
        else:
            # A single entity, not a collection. There is nothing to page.
            return document
        if not isinstance(page, list):
            raise EvalError("OData.Feed: 'value' is not an array")
        rows.extend(row if isinstance(row, dict) else {"Value": row} for row in page)

        nxt = _odata_next_link(document, landed)
        if nxt is None:
            return rows
        current = nxt
        # The next URL is chosen by the REMOTE SERVER and can name any host.
        # Carrying the caller's headers there hands whatever `Authorization`
        # they set to a host they never named - a token exfiltrated in one
        # hop by a hostile or compromised feed. Cross-origin, only the
        # Accept header travels, which is what a browser does with
        # credentials on a cross-origin redirect. A 401 from the far side is
        # a typed error naming the URL, so the caller is not left guessing.
        page_headers = (
            headers
            if _origin(current) == origin
            else {"Accept": headers.get("Accept", "application/json")}
        )
        page_options = {"Headers": dict(page_headers)}
        if "Timeout" in options:
            page_options["Timeout"] = options["Timeout"]

    raise EvalError(
        f"OData.Feed: stopped after {_MAX_ODATA_PAGES} pages without reaching "
        f"the end of {url}; returning part of a feed as if it were all of it "
        "would be a wrong answer, so this refuses instead"
    )


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


def _credentials(
    options: dict[str, Any], prefix: str, what: str
) -> tuple[str | None, str | None]:
    """Username/password from the environment, and ONLY from the environment.

    The README states twice, as a security property, that "credentials come
    from the environment, not from the M file". The code accepted
    ``[Username=..., Password=...]`` out of the record and preferred them
    OVER the environment, so the documented guarantee was the opposite of
    the behaviour. Neither name is a documented Power Query option either -
    the connectors' pages list no credential options at all, because real
    Power Query takes credentials from its credential store - so honouring
    them also invented two options Microsoft does not have.

    They are refused by name rather than ignored: a query that carries a
    password must fail loudly, or the password stays in the file and the
    author believes it is doing something.
    """
    for option, suffix in (("Username", "USER"), ("Password", "PASSWORD")):
        if option in options:
            raise UnsupportedError(
                f"{what}: {option} is not read from the M file; set "
                f"{prefix}_{suffix} in the environment instead, because a "
                "credential written into a query gets committed"
            )
    user = os.environ.get(f"{prefix}_USER")
    password = os.environ.get(f"{prefix}_PASSWORD")
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


def _require_logical(value: Any, option: str, what: str) -> bool:
    """A documented ``logical (true/false)`` option, refused if it is not one."""
    if not isinstance(value, bool):
        raise EvalError(f"{what}: {option} must be a logical, got {_type_name(value)}")
    return value


def _sql_server_options(
    options: dict[str, Any], what: str
) -> tuple[float | None, float | None, bool]:
    """The Sql.Database options this build can honour; returns (connect, command, msf).

    All four were being POPPED AND DISCARDED. A query that set
    ``[CommandTimeout = ...]`` ran with no timeout, one that set
    ``[MultiSubnetFailover = true]`` got a connection string without it, and
    one that asked for ``[HierarchicalNavigation = true]`` got the flat
    navigation table anyway - three wrong answers a caller could not tell
    from right ones. `Odbc.Query` in this same file had validated and
    applied its `CommandTimeout` the whole time, so the two connector
    families disagreed about the same documented option.

    ``HierarchicalNavigation`` is accepted only as `false`. Its documented
    default IS false, so `false` describes exactly what this build returns;
    `true` asks for tables grouped by schema, and the shape of that grouped
    table is not something Microsoft's page specifies - inventing one would
    be the same defect class as an invented arity, so it is refused by name.
    """
    connect = options.pop("ConnectionTimeout", None)
    command = options.pop("CommandTimeout", None)
    hierarchical = options.pop("HierarchicalNavigation", None)
    multi_subnet = options.pop("MultiSubnetFailover", None)

    if hierarchical is not None and _require_logical(
        hierarchical, "HierarchicalNavigation", what
    ):
        raise UnsupportedError(
            f"{what}: HierarchicalNavigation = true groups the navigation "
            "table by schema; this build returns the flat table, which is "
            "the documented default (false)"
        )
    return (
        _request_timeout(connect, 15.0, what, "ConnectionTimeout")
        if connect is not None
        else None,
        _request_timeout(command, 600.0, what, "CommandTimeout")
        if command is not None
        else None,
        multi_subnet is not None
        and _require_logical(multi_subnet, "MultiSubnetFailover", what),
    )


def _deferred_query(
    driver: Any,
    connection_string: str,
    sql: Callable[[], str],
    what: str,
    connect_timeout: float | None = None,
    command_timeout: float | None = None,
) -> _DeferredRows:
    """A navigation row's `Data`: one query, run only if the row is selected.

    The connection is opened and closed inside the fetch, so a navigation
    table that is never selected from holds nothing open, and selecting one
    table does not keep a connection alive for the others. The timeouts come
    along because this connection - not the catalog one - is where the read
    the caller asked to bound actually happens.
    """

    def fetch() -> list[dict[str, Any]]:
        # The statement is BUILT here, not at navigation time. Building it
        # eagerly meant one unusable catalog name (a NUL) refused the whole
        # `Sql.Database(...)` call, so no table could be selected - the same
        # all-or-nothing coupling the deferral exists to prevent for a table
        # that merely fails to read.
        statement = sql()
        connection = _odbc_connect(driver, connection_string, connect_timeout)
        try:
            if command_timeout is not None:
                connection.timeout = _timeout_int(command_timeout)
            return _run_query(connection, statement)
        finally:
            connection.close()

    return _DeferredRows(fetch, what)


def _sql_identifier(name: str, what: str) -> str:
    """A catalog name as a SQL Server identifier: quoted, with `"` doubled.

    The navigation SELECT wrapped INFORMATION_SCHEMA names in quotes and did
    nothing else, so a name carrying a `"` closed the identifier early and
    the rest of the name ran as SQL - reading a different table than the
    query named, or a second statement - under the rights of the account
    pqtools connects with, which need not be the account that named the
    table. Doubling the quote is the SQL standard and what SQL Server reads
    under QUOTED_IDENTIFIER, which ODBC turns on.
    """
    if "\x00" in name:
        raise EvalError(f"{what}: catalog name contains a NUL byte: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def _navigation_sql(schema: str, table: str) -> Callable[[], str]:
    """One navigation row's SELECT, built when that row is read.

    Bound per row and deferred, so a name this cannot express refuses only
    the row that owns it. Built eagerly in the comprehension, one such name
    refused `Sql.Database(...)` itself and no table could be selected.
    """

    def build() -> str:
        return (
            "SELECT * FROM "
            f"{_sql_identifier(schema, 'Sql.Database')}."
            f"{_sql_identifier(table, 'Sql.Database')}"
        )

    return build


def _sql_database(args: list[Any], ctx: _Ctx) -> Any:
    """``Sql.Database(server, database, options)`` - SQL Server.

    With ``[Query="..."]`` the statement runs and its rows come back. Without
    it, the navigation table of tables and views comes back, which is what
    ``Source{[Schema="dbo",Item="Orders"]}[Data]`` walks.

    No folding: later ``Table.SelectRows`` steps filter locally rather than
    becoming a WHERE clause. Same rows, more bytes over the wire.
    """
    _arity("Sql.Database", args, 2, 3)
    _policy(ctx).check_db(what="Sql.Database")
    server = _require_str(args[0])
    database = _require_str(args[1])
    options = _optional_record(args[2] if len(args) == 3 else None, "Sql.Database")

    query = options.pop("Query", None)
    user, password = _credentials(options, "PQTOOLS_SQL", "Sql.Database")
    connection_string = options.pop("ConnectionString", None)
    connect_timeout, command_timeout, multi_subnet = _sql_server_options(
        options, "Sql.Database"
    )
    if options:
        raise UnsupportedError(f"Sql.Database: option(s) {sorted(options)}")

    pyodbc = _require_driver("pyodbc", "sql", "Sql.Database")
    if connection_string is None:
        parts = [
            "DRIVER={ODBC Driver 18 for SQL Server}",
            f"SERVER={_odbc_escape(server)}",
            f"DATABASE={_odbc_escape(database)}",
            "TrustServerCertificate=yes",
        ]
        if multi_subnet:
            # "sets the value of the 'MultiSubnetFailover' property in the
            # connection string ... It also sets ApplicationIntent=readonly"
            # - sql-database, verbatim. The second half is easy to miss and
            # changes which replica the query lands on.
            parts += ["MultiSubnetFailover=Yes", "ApplicationIntent=ReadOnly"]
        if user:
            parts += [
                f"UID={_odbc_escape(user)}",
                f"PWD={_odbc_escape(password or '')}",
            ]
        else:
            parts.append("Trusted_Connection=yes")
        connection_string = ";".join(parts)
    elif multi_subnet:
        raise UnsupportedError(
            "Sql.Database: MultiSubnetFailover sets a connection-string "
            "property, so it cannot be combined with an explicit "
            "ConnectionString; put MultiSubnetFailover=Yes and "
            "ApplicationIntent=ReadOnly in that string instead"
        )
    elif user or password:
        # Same shape as the refusal above, and it was missing: UID/PWD are
        # only appended in the branch that BUILDS the string, so with an
        # explicit ConnectionString the environment credentials were read
        # and then silently dropped. A caller who set PQTOOLS_SQL_PASSWORD
        # would have connected as somebody else without being told.
        raise UnsupportedError(
            "Sql.Database: PQTOOLS_SQL_USER/PQTOOLS_SQL_PASSWORD are set but "
            "cannot be applied to an explicit ConnectionString; put UID and "
            "PWD in that string, or unset the variables"
        )

    connection = _odbc_connect(pyodbc, str(connection_string), connect_timeout)
    try:
        if command_timeout is not None:
            connection.timeout = _timeout_int(command_timeout)
        if query is not None:
            return _run_query(connection, _require_str(query))
        catalog = _run_query(
            connection,
            "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
            "FROM INFORMATION_SCHEMA.TABLES",
        )
    finally:
        connection.close()
    # Each row's `Data` is DEFERRED. It used to be a completed `SELECT *` for
    # every table in the catalog, run before the query said which one it
    # wanted, so `Source{[Item="Orders"]}[Data]` on a small table paid for
    # every other table beside it - and failed if any of them failed. The
    # read now happens when the field is selected, on its own short-lived
    # connection, so nothing is left open waiting to be used.
    return [
        {
            "Schema": row["TABLE_SCHEMA"],
            "Item": row["TABLE_NAME"],
            "Name": row["TABLE_NAME"],
            "Kind": "View" if row["TABLE_TYPE"] == "VIEW" else "Table",
            "Data": _deferred_query(
                pyodbc,
                str(connection_string),
                _navigation_sql(row["TABLE_SCHEMA"], row["TABLE_NAME"]),
                f"Sql.Database: {row['TABLE_SCHEMA']}.{row['TABLE_NAME']}",
                connect_timeout,
                command_timeout,
            ),
        }
        for row in catalog
    ]


def _oracle_database(args: list[Any], ctx: _Ctx) -> Any:
    """``Oracle.Database(server as text, optional options as nullable record)``.

    Oracle does NOT share PostgreSQL's and MySQL's shape, and treating it as
    if it did was an invented arity - the same defect class as an invented
    function name, and one this package's own catalog gate cannot see
    because the NAME is real. Microsoft's Syntax block, verbatim:

        Oracle.Database(server as text, optional options as nullable record)
        PostgreSQL.Database(server as text, database as text, optional ...)
        MySQL.Database(server as text, database as text, optional ...)

    There is no `database` argument. So the documented call
    ``Oracle.Database("host", [Query = "select ..."])`` read the options
    record as the database name and died on "expected text, got record",
    while the three-argument form pqtools had made up was the only one that
    reached a driver.

    `server` is passed to `oracledb` as the DSN unchanged. Oracle addresses
    a *service*, not a database, and its EZConnect spelling
    (``host:port/service``) already carries the port the docs say may be
    appended - splitting it here to rebuild it would only be a chance to get
    it wrong.
    """
    _arity("Oracle.Database", args, 1, 2)
    _policy(ctx).check_db(what="Oracle.Database")
    server = _require_str(args[0])
    options = _optional_record(args[1] if len(args) == 2 else None, "Oracle.Database")

    query = options.pop("Query", None)
    user, password = _credentials(options, "PQTOOLS_ORACLE", "Oracle.Database")
    if options:
        raise UnsupportedError(f"Oracle.Database: option(s) {sorted(options)}")
    if query is None:
        raise UnsupportedError(
            "Oracle.Database: navigation without a Query option is not "
            'implemented; pass [Query="select ..."]'
        )

    driver = _require_driver("oracledb", "oracle", "Oracle.Database")
    connection = driver.connect(user=user, password=password, dsn=server)
    try:
        return _run_query(connection, _require_str(query))
    finally:
        connection.close()


def _generic_database(
    name: str, module: str, extra: str, env: str, default_port: int
) -> Any:
    """One implementation for the two DB-API connectors that differ only in driver.

    PostgreSQL and MySQL genuinely do share a shape - `(server, database,
    optional options)` and a `host`/`port`/`user`/`password`/`database`
    connect call. Oracle does not, and shoehorning it in here is what
    produced a connector whose documented call could not be made; it has
    its own function above.
    """

    def connector(args: list[Any], ctx: _Ctx) -> Any:
        # `(server as text, database as text, optional options as nullable
        # record)` - both pages, verbatim. `database` is NOT optional. A
        # one-argument call used to be accepted and connected with
        # `database=""`, which PostgreSQL resolves to the user's default
        # database: a different database, silently, on a call the signature
        # does not permit.
        _arity(name, args, 2, 3)
        _policy(ctx).check_db(what=name)
        server = _require_str(args[0])
        database = _require_str(args[1])
        options = _optional_record(args[2] if len(args) == 3 else None, name)

        query = options.pop("Query", None)
        user, password = _credentials(options, env, name)
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


def _odbc_escape(value: str) -> str:
    """Quote one connection-string VALUE so its contents stay a value.

    Values were interpolated raw. A password of ``x;Encrypt=no`` therefore
    did not produce a wrong password - it produced an extra connection-string
    keyword, and that particular one turns TLS off. Server, database and user
    were injectable the same way, and a password legitimately containing `;`
    or `}` could not be expressed at all.

    Braces are ODBC's quoting mechanism; a `}` inside a braced value is
    written twice. Only values are escaped - the literal `DRIVER={...}`
    written by this module is already a quoted value and must not be quoted
    again.
    """
    if value and (value != value.strip() or any(c in value for c in ";{}=")):
        return "{" + value.replace("}", "}}") + "}"
    return value


def _odbc_connection_string(value: Any, what: str) -> str:
    """``connectionString as any`` - text, or a record of property pairs.

    "connectionString can be text or a record of property value pairs.
    Property values can either be text or number" - Odbc.Query, verbatim.
    Only the text half was accepted, so the record spelling failed inside
    `_require_str` before any connection was attempted.
    """
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                raise EvalError(
                    f"{what}: connection property {key!r} must be text or a "
                    f"number, got {_type_name(item)}"
                )
            # Escaping only the VALUE was half a fix. An M record field name
            # can be any quoted identifier, so
            # `Odbc.Query([#"UID=sa;Encrypt" = "no"], ...)` put three keywords
            # into the string through the KEY. A real connection-string
            # keyword cannot contain these, so this refuses rather than
            # quoting something that was never a valid key.
            if any(character in str(key) for character in ";={}"):
                raise EvalError(
                    f"{what}: connection property name {key!r} contains a "
                    "connection-string delimiter (one of ; = { })"
                )
            parts.append(f"{key}={_odbc_escape(str(item))}")
        return ";".join(parts)
    return _require_str(value)


def _odbc_options(
    options: dict[str, Any], what: str
) -> tuple[float | None, float | None]:
    """The three documented Odbc.* options; returns (connect, command) seconds.

    ConnectionTimeout and CommandTimeout are DURATIONS ("A duration that
    controls how long to wait..."), which is why they share Web.Contents'
    `_request_timeout` rather than being passed to `float()`.
    """
    connect = options.pop("ConnectionTimeout", None)
    command = options.pop("CommandTimeout", None)
    windows_auth = options.pop("SqlCompatibleWindowsAuth", None)
    if windows_auth is not None:
        if not isinstance(windows_auth, bool):
            raise EvalError(
                f"{what}: SqlCompatibleWindowsAuth must be a logical, got "
                f"{_type_name(windows_auth)}"
            )
        if not windows_auth:
            # Its documented default is true; false suppresses connection
            # string options this build never adds, so honouring it would be
            # a no-op dressed as a setting.
            raise UnsupportedError(
                f"{what}: SqlCompatibleWindowsAuth = false shapes SQL Server "
                "Windows-authentication options that are not generated here"
            )
    if options:
        raise UnsupportedError(f"{what}: option(s) {sorted(options)}")
    return (
        _request_timeout(connect, 15.0, what, "ConnectionTimeout")
        if connect is not None
        else None,
        _request_timeout(command, 600.0, what, "CommandTimeout")
        if command is not None
        else None,
    )


def _timeout_int(seconds: float) -> int:
    """Whole seconds for a driver, never rounding a real bound down to zero.

    pyodbc reads `timeout = 0` as NO TIMEOUT. `int(0.5)` is 0, so asking for
    a TIGHTER bound than one second removed the bound entirely - a caller who
    wrote `#duration(0,0,0,0.5)` got an unbounded query where they had asked
    for a half-second one. Rounding up is the only direction that cannot turn
    a limit into its absence.
    """
    return max(1, math.ceil(seconds))


def _odbc_connect(
    pyodbc: Any, connection_string: str, connect_timeout: float | None
) -> Any:
    if connect_timeout is None:
        return pyodbc.connect(connection_string)
    return pyodbc.connect(connection_string, timeout=_timeout_int(connect_timeout))


def _odbc_query(args: list[Any], ctx: _Ctx) -> Any:
    """``Odbc.Query(connectionString, query, options)`` - any ODBC source."""
    # `optional options as nullable record` was missing from the signature,
    # and connectionString is `any` (text OR a record of property pairs) -
    # both halves of the documented call failed before connecting.
    _arity("Odbc.Query", args, 2, 3)
    _policy(ctx).check_db(what="Odbc.Query")
    connection_string = _odbc_connection_string(args[0], "Odbc.Query")
    query = _require_str(args[1])
    options = _optional_record(args[2] if len(args) == 3 else None, "Odbc.Query")
    connect_timeout, command_timeout = _odbc_options(options, "Odbc.Query")
    pyodbc = _require_driver("pyodbc", "sql", "Odbc.Query")
    connection = _odbc_connect(pyodbc, connection_string, connect_timeout)
    try:
        if command_timeout is not None:
            connection.timeout = _timeout_int(command_timeout)
        return _run_query(connection, query)
    finally:
        connection.close()


def _odbc_datasource(args: list[Any], ctx: _Ctx) -> Any:
    """``Odbc.DataSource(connectionString, options)`` - the table nav list."""
    _arity("Odbc.DataSource", args, 1, 2)
    _policy(ctx).check_db(what="Odbc.DataSource")
    # Same `connectionString as any` as Odbc.Query - the record spelling was
    # refused here too.
    connection_string = _odbc_connection_string(args[0], "Odbc.DataSource")
    options = _optional_record(args[1] if len(args) == 2 else None, "Odbc.DataSource")
    query = options.pop("Query", None)
    connect_timeout, command_timeout = _odbc_options(options, "Odbc.DataSource")
    pyodbc = _require_driver("pyodbc", "sql", "Odbc.DataSource")
    connection = _odbc_connect(pyodbc, connection_string, connect_timeout)
    if command_timeout is not None:
        connection.timeout = _timeout_int(command_timeout)
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


# Default ports, so an absent port reports the number the scheme implies
# rather than a blank. Microsoft's own Uri.Parts example prints `Port = 80`
# for a URI that names no port at all.
_DEFAULT_PORTS = {"http": 80, "https": 443, "ftp": 21, "ftps": 990}


def _uri_parts(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Uri.Parts", args, 1)
    text = _require_str(args[0])
    parts = urllib.parse.urlsplit(text)
    if not parts.scheme or not parts.netloc:
        # `Uri.Parts("www.adventure-works.com")` is documented to report
        # Scheme = "http" and Host = "www.adventure-works.com". Without this,
        # urlsplit reads the whole string as a PATH, so the host came back
        # empty and the host name appeared in Path - a wrong answer with no
        # error, on the page's own first example.
        parts = urllib.parse.urlsplit("http://" + text.lstrip("/"))
    port: Any = parts.port
    if port is None:
        port = _DEFAULT_PORTS.get(parts.scheme, "")
    return {
        "Scheme": parts.scheme,
        "Host": parts.hostname or "",
        "Port": port,
        # An empty path is "/" - again the page's own example.
        "Path": parts.path or "/",
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
    "Oracle.Database": _oracle_database,
    "Odbc.Query": _odbc_query,
    "Odbc.DataSource": _odbc_datasource,
    "Uri.Parts": _uri_parts,
    "Uri.Combine": _uri_combine,
    "Uri.EscapeDataString": _uri_escape,
    "Uri.BuildQueryString": _uri_build,
}
