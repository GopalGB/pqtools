"""Connectors that leave the machine, and the gate in front of them.

Every test here runs against a local HTTP server or the local filesystem.
Nothing reaches the real internet: a suite that needs the network is a suite
that fails on a plane, and one that silently skips is worse than one that
fails, because it reports green while testing nothing.

The gate tests matter more than the happy paths. `Web.Contents` working is a
feature; `Web.Contents` refusing to work unasked is the reason the feature is
safe to ship, since the M source names the URL and the M source is often
somebody else's file.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from pqtools import evaluate
from pqtools.io import IOBlockedError, IOPolicy


@pytest.fixture(scope="module")
def server() -> Any:
    """A real HTTP server on localhost, so the client path is genuinely exercised."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/csv"):
                body = b"a,b\n1,2\n3,4\n"
                kind = "text/csv"
            elif self.path.startswith("/odata"):
                body = json.dumps(
                    {"value": [{"Id": 1, "Name": "x"}, {"Id": 2, "Name": "y"}]}
                ).encode()
                kind = "application/json"
            elif self.path.startswith("/echo"):
                body = self.path.encode()
                kind = "text/plain"
            elif self.path.startswith("/boom"):
                self.send_error(500)
                return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


# The local test server lives on loopback, which the default policy blocks on
# purpose (see the SSRF tests below). Tests that exercise the HTTP client
# therefore opt in explicitly, which also keeps the opt-in itself covered.
NET = IOPolicy(allow_net=True, allow_private=True)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_web_contents_is_refused_by_default(server: str) -> None:
    with pytest.raises(IOBlockedError) as caught:
        evaluate(f'let S = Web.Contents("{server}/csv") in S')
    # The message has to name the flag. An error that says only "blocked"
    # sends the reader to the source code to find out what to do.
    assert "--allow-net" in str(caught.value)


def test_database_is_refused_by_default() -> None:
    with pytest.raises(IOBlockedError) as caught:
        evaluate('let S = Sql.Database("host", "db") in S')
    assert "--allow-db" in str(caught.value)


def test_host_allowlist_blocks_an_unlisted_host(server: str) -> None:
    policy = IOPolicy(
        allow_net=True, allow_private=True, hosts=frozenset({"example.com"})
    )
    with pytest.raises(IOBlockedError, match="not in the allowed set"):
        evaluate(f'let S = Web.Contents("{server}/csv") in S', io=policy)


def test_host_allowlist_permits_a_listed_host(server: str) -> None:
    policy = IOPolicy(
        allow_net=True, allow_private=True, hosts=frozenset({"127.0.0.1"})
    )
    result = evaluate(
        f'let S = Text.FromBinary(Web.Contents("{server}/csv")) in S', io=policy
    )
    assert result.startswith("a,b")


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://host/x", "gopher://host/x"]
)
def test_non_http_schemes_never_pass_the_network_gate(url: str) -> None:
    # This is the pivot that turns "allowed to fetch a URL" into "allowed to
    # read any local file", so it is checked separately from the host rules.
    with pytest.raises(IOBlockedError, match="only http and https"):
        evaluate(f'let S = Web.Contents("{url}") in S', io=NET)


# --------------------------------------------------------------------------
# Web.Contents
# --------------------------------------------------------------------------


def test_web_contents_returns_binary(server: str) -> None:
    # M types this as binary, not text; Csv.Document and Json.Document both
    # expect bytes, so returning str here would break every real query.
    result = evaluate(f'let S = Web.Contents("{server}/csv") in S', io=NET)
    assert isinstance(result, bytes)


def test_csv_over_http_end_to_end(server: str) -> None:
    source = f"""
    let
        Source   = Csv.Document(Web.Contents("{server}/csv")),
        Promoted = Table.PromoteHeaders(Source)
    in
        Promoted
    """
    assert evaluate(source, io=NET) == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
    ]


def test_relative_path_and_query_options_shape_the_url(server: str) -> None:
    source = (
        f'let S = Text.FromBinary(Web.Contents("{server}", '
        '[RelativePath="echo", Query=[q="1"]])) in S'
    )
    assert evaluate(source, io=NET) == "/echo?q=1"


def test_http_error_status_is_reported_not_swallowed(server: str) -> None:
    from pqtools import EvalError

    with pytest.raises(EvalError, match="HTTP 500"):
        evaluate(f'let S = Web.Contents("{server}/boom") in S', io=NET)


def test_manual_status_handling_is_refused_rather_than_ignored(server: str) -> None:
    from pqtools import UnsupportedError

    # Silently ignoring this would make a query that tolerates a 404 look like
    # it succeeded, which is the wrong-answer-shaped-right failure mode.
    with pytest.raises(UnsupportedError, match="ManualStatusHandling"):
        source = (
            f'let S = Web.Contents("{server}/csv", [ManualStatusHandling={{404}}]) in S'
        )
        evaluate(source, io=NET)


def test_odata_feed_reads_the_value_array(server: str) -> None:
    result = evaluate(f'let S = OData.Feed("{server}/odata") in S', io=NET)
    assert result == [{"Id": 1, "Name": "x"}, {"Id": 2, "Name": "y"}]


# --------------------------------------------------------------------------
# Folder.*
# --------------------------------------------------------------------------


def test_folder_files_lists_recursively(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.csv").write_text("b")
    rows = evaluate(f'let S = Folder.Files("{tmp_path}") in S')
    assert sorted(r["Name"] for r in rows) == ["a.txt", "b.csv"]
    assert {r["Extension"] for r in rows} == {".txt", ".csv"}


def test_folder_contents_is_shallow_and_includes_directories(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.csv").write_text("b")
    rows = evaluate(f'let S = Folder.Contents("{tmp_path}") in S')
    assert sorted(r["Name"] for r in rows) == ["a.txt", "sub"]


def test_folder_files_needs_no_network_permission(tmp_path: Path) -> None:
    # Local disk is not the network. Gating it behind --allow-net would push
    # people to pass the flag habitually, which defeats the flag.
    (tmp_path / "a.txt").write_text("a")
    assert evaluate(f'let S = Folder.Files("{tmp_path}") in S')


def test_missing_folder_names_the_path_and_the_workaround(tmp_path: Path) -> None:
    from pqtools import EvalError

    with pytest.raises(EvalError, match="--bind"):
        evaluate(f'let S = Folder.Files("{tmp_path / "nope"}") in S')


# --------------------------------------------------------------------------
# Uri.* - no IO, so no gate
# --------------------------------------------------------------------------


def test_uri_parts_splits_a_url() -> None:
    parts = evaluate('let S = Uri.Parts("https://h.example:8080/p?a=1#f") in S')
    assert parts["Scheme"] == "https"
    assert parts["Host"] == "h.example"
    assert parts["Port"] == 8080
    assert parts["Query"] == {"a": "1"}


def test_uri_escaping() -> None:
    assert evaluate('let S = Uri.EscapeDataString("a b&c") in S') == "a%20b%26c"


def test_there_is_no_uri_unescape_because_m_has_none() -> None:
    """pqtools shipped `Uri.UnescapeDataString` until 0.10.0. M has no such
    function - Microsoft documents exactly four Uri functions and that is not
    one. Implementing a function M lacks is worse than omitting one: the query
    passes here and fails in Power Query, which inverts this package's whole
    promise. Uri.Parts already returns decoded fields.
    """
    from pqtools import UnsupportedError

    with pytest.raises(UnsupportedError, match="unknown identifier"):
        evaluate('Uri.UnescapeDataString("a%20b")')


def test_uri_combine_joins_a_relative_path() -> None:
    assert (
        evaluate('let S = Uri.Combine("https://h.example/api", "v1/x") in S')
        == "https://h.example/api/v1/x"
    )


# --------------------------------------------------------------------------
# Excel.Workbook
# --------------------------------------------------------------------------


def _workbook(path: Path) -> Path:
    # Built here rather than read from .samples/, which is gitignored: a test
    # that depends on an untracked file passes for the author and fails in CI.
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sales"
    for row in (["Region", "Amount"], ["North", 10], ["South", 20]):
        sheet.append(row)
    book.create_sheet("Empty")
    book.save(path)
    return path


def test_excel_workbook_lists_sheets(tmp_path: Path) -> None:
    book = _workbook(tmp_path / "b.xlsx")
    rows = evaluate(f'let S = Excel.Workbook(File.Contents("{book}")) in S')
    assert [r["Name"] for r in rows] == ["Sales", "Empty"]
    assert {r["Kind"] for r in rows} == {"Sheet"}


def test_excel_workbook_navigates_to_a_sheet_with_headers(tmp_path: Path) -> None:
    book = _workbook(tmp_path / "b.xlsx")
    source = f"""
    let
        Source = Excel.Workbook(File.Contents("{book}"), true),
        Sales  = Table.SelectRows(Source, each [Item] = "Sales"),
        Data   = Table.Column(Sales, "Data")
    in
        Data{{0}}
    """
    assert evaluate(source) == [
        {"Region": "North", "Amount": 10},
        {"Region": "South", "Amount": 20},
    ]


def test_excel_workbook_rejects_a_non_binary_argument() -> None:
    from pqtools import EvalError

    with pytest.raises(EvalError, match="expected binary"):
        evaluate('let S = Excel.Workbook("not bytes") in S')


# --------------------------------------------------------------------------
# SSRF - what --allow-net must still refuse
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS/GCP/Azure metadata
        # metadata.google.internal is deliberately NOT here: it resolves only
        # inside GCP, so off-cloud this test would pass because DNS failed
        # rather than because the gate worked. A test whose result depends on
        # where it runs reports confidence it has not earned.
        "http://127.0.0.1:8080/admin",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://[::1]:8080/",
    ],
)
def test_allow_net_does_not_permit_reaching_internal_addresses(url: str) -> None:
    """--allow-net means "fetch from the internet", not "probe my network".

    169.254.169.254 is the one that matters: on every major cloud it is the
    instance metadata service, and a successful read hands over the host's
    credentials. A workbook someone emailed you is exactly the vehicle for
    that request, and the URL lives in the workbook, not in the command line.
    """
    with pytest.raises(IOBlockedError, match="internal address|cannot reach|no host"):
        evaluate(f'let S = Web.Contents("{url}") in S', io=IOPolicy(allow_net=True))


def test_internal_addresses_are_reachable_when_explicitly_permitted(
    server: str,
) -> None:
    # The escape hatch has to work, or every legitimate intranet user is stuck.
    result = evaluate(
        f'let S = Text.FromBinary(Web.Contents("{server}/csv")) in S',
        io=IOPolicy(allow_net=True, allow_private=True),
    )
    assert result.startswith("a,b")


def test_a_hostname_pointing_at_an_internal_address_is_also_blocked() -> None:
    # Blocking only literal IPs would be defeated by a single DNS record, so
    # the check resolves the name first.
    with pytest.raises(IOBlockedError, match="internal address"):
        evaluate(
            'let S = Web.Contents("http://localhost:9/") in S',
            io=IOPolicy(allow_net=True),
        )
