"""OData server-driven paging: the whole feed, or a typed refusal.

`OData.Feed` used to fetch once, return the first page's `value`, and drop
`@odata.nextLink`. A two-page feed therefore produced half its rows with no
error - the same shape of wrong answer the module's Atom/XML refusal already
existed to prevent, except silent.

These run against a real local HTTP server so the client path, the redirect
handler and the network policy are all genuinely exercised.
"""

from __future__ import annotations

import http.server
import json
import threading
from typing import Any

import pytest

from pqtools import EvalError, evaluate
from pqtools.io import IOBlockedError, IOPolicy

NET = IOPolicy(allow_net=True, allow_private=True)


class _Feed:
    """A paging feed whose link shape each test chooses."""

    def __init__(self) -> None:
        self.pages: dict[str, bytes] = {}
        self.hits: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.fail: set[str] = set()
        self.redirects: dict[str, str] = {}


@pytest.fixture
def feed() -> Any:
    state = _Feed()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state.hits.append(self.path)
            state.headers.append(dict(self.headers))
            if self.path in state.fail:
                self.send_error(503)
                return
            if self.path in state.redirects:
                self.send_response(302)
                self.send_header("Location", state.redirects[self.path])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = state.pages.get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{httpd.server_port}"  # type: ignore[attr-defined]
    yield state
    httpd.shutdown()
    thread.join(timeout=5)


def _page(rows: list[dict[str, Any]], next_link: str | None = None) -> bytes:
    body: dict[str, Any] = {"value": rows}
    if next_link is not None:
        body["@odata.nextLink"] = next_link
    return json.dumps(body).encode()


def _feed_call(base: str, path: str = "/odata") -> str:
    return f'let S = OData.Feed("{base}{path}") in S'


# --- the defect: every row, not just page one ----------------------------


def test_every_page_is_returned_with_an_absolute_next_link(feed: Any) -> None:
    base = feed.base
    feed.pages["/odata"] = _page([{"Id": 1}], f"{base}/odata?page=2")
    feed.pages["/odata?page=2"] = _page([{"Id": 2}], f"{base}/odata?page=3")
    feed.pages["/odata?page=3"] = _page([{"Id": 3}])

    assert evaluate(_feed_call(base), io=NET) == [{"Id": 1}, {"Id": 2}, {"Id": 3}]
    assert feed.hits == ["/odata", "/odata?page=2", "/odata?page=3"]


def test_a_relative_next_link_is_resolved_against_the_page_that_carried_it(
    feed: Any,
) -> None:
    # Servers are allowed to send a relative nextLink; using it verbatim
    # would produce a request to a path that does not exist.
    feed.pages["/odata"] = _page([{"Id": 1}], "odata?page=2")
    feed.pages["/odata?page=2"] = _page([{"Id": 2}])

    assert evaluate(_feed_call(feed.base), io=NET) == [{"Id": 1}, {"Id": 2}]
    assert feed.hits == ["/odata", "/odata?page=2"]


def test_the_v3_spelling_of_the_next_link_is_followed(feed: Any) -> None:
    body = json.dumps(
        {"value": [{"Id": 1}], "odata.nextLink": f"{feed.base}/odata?page=2"}
    ).encode()
    feed.pages["/odata"] = body
    feed.pages["/odata?page=2"] = _page([{"Id": 2}])

    assert evaluate(_feed_call(feed.base), io=NET) == [{"Id": 1}, {"Id": 2}]


def test_a_single_page_feed_still_makes_exactly_one_request(feed: Any) -> None:
    feed.pages["/odata"] = _page([{"Id": 1}, {"Id": 2}])
    assert evaluate(_feed_call(feed.base), io=NET) == [{"Id": 1}, {"Id": 2}]
    assert feed.hits == ["/odata"]


# --- failures on a later page are errors, never a truncated success ------


def test_a_failing_second_page_raises_rather_than_returning_page_one(
    feed: Any,
) -> None:
    feed.pages["/odata"] = _page([{"Id": 1}], f"{feed.base}/odata?page=2")
    feed.fail.add("/odata?page=2")

    with pytest.raises(EvalError, match="HTTP 503"):
        evaluate(_feed_call(feed.base), io=NET)


def test_a_missing_second_page_raises(feed: Any) -> None:
    feed.pages["/odata"] = _page([{"Id": 1}], f"{feed.base}/odata?page=missing")
    with pytest.raises(EvalError, match="HTTP 404"):
        evaluate(_feed_call(feed.base), io=NET)


def test_an_unusable_next_link_is_refused_not_ignored(feed: Any) -> None:
    body = json.dumps({"value": [{"Id": 1}], "@odata.nextLink": 42}).encode()
    feed.pages["/odata"] = body
    with pytest.raises(EvalError, match="not a usable URL"):
        evaluate(_feed_call(feed.base), io=NET)


def test_a_next_page_that_is_not_a_collection_is_refused(feed: Any) -> None:
    feed.pages["/odata"] = _page([{"Id": 1}], f"{feed.base}/entity")
    feed.pages["/entity"] = json.dumps({"Id": 99}).encode()
    with pytest.raises(EvalError, match="not a collection"):
        evaluate(_feed_call(feed.base), io=NET)


# --- cycles and ceilings -------------------------------------------------


def test_a_self_referencing_page_is_detected_as_a_cycle(feed: Any) -> None:
    feed.pages["/odata"] = _page([{"Id": 1}], f"{feed.base}/odata")
    with pytest.raises(EvalError, match="cycle"):
        evaluate(_feed_call(feed.base), io=NET)
    assert len(feed.hits) == 1


def test_a_next_link_back_to_the_landed_url_is_a_cycle_on_the_first_hop(
    feed: Any,
) -> None:
    """Page one is requested at /odata and served, after a 302, from /landed.

    `seen` recorded only the REQUESTED url, so a next link naming the landed
    one was not yet a known page: it was fetched a second time, identically,
    and only then caught. Bounded by the page ceiling either way - but the
    cycle check exists so that the ceiling is never what stops a loop.
    """
    base = feed.base
    feed.redirects["/odata"] = "/landed"
    feed.pages["/landed"] = _page([{"Id": 1}], f"{base}/landed")
    with pytest.raises(EvalError, match="cycle"):
        evaluate(_feed_call(base), io=NET)
    # one redirect, one real page, and no second fetch of the page it landed on
    assert feed.hits == ["/odata", "/landed"]


def test_a_two_page_loop_is_detected_as_a_cycle(feed: Any) -> None:
    feed.pages["/odata"] = _page([{"Id": 1}], f"{feed.base}/odata?p=2")
    feed.pages["/odata?p=2"] = _page([{"Id": 2}], f"{feed.base}/odata")
    with pytest.raises(EvalError, match="cycle"):
        evaluate(_feed_call(feed.base), io=NET)


def test_an_endless_feed_stops_at_the_page_ceiling(
    feed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pqtools.builtins import _sources

    monkeypatch.setattr(_sources, "_MAX_ODATA_PAGES", 3)
    for n in range(10):
        feed.pages[f"/odata?p={n}"] = _page([{"Id": n}], f"{feed.base}/odata?p={n + 1}")
    with pytest.raises(EvalError, match="stopped after 3 pages"):
        evaluate(f'let S = OData.Feed("{feed.base}/odata?p=0") in S', io=NET)
    assert len(feed.hits) == 3


# --- the policy is re-checked per request, not once for the first URL ----


def test_a_next_link_to_a_blocked_host_is_refused(feed: Any) -> None:
    # The first URL is allowed; the second points somewhere the policy does
    # not permit. Checking only the entry URL would follow it anyway.
    feed.pages["/odata"] = _page([{"Id": 1}], "http://169.254.169.254/latest/meta-data")
    scoped = IOPolicy(
        allow_net=True, allow_private=True, hosts=frozenset({"127.0.0.1"})
    )
    with pytest.raises(IOBlockedError):
        evaluate(_feed_call(feed.base), io=scoped)
    # the link-local address was never requested
    assert feed.hits == ["/odata"]


def test_paging_is_still_blocked_entirely_without_allow_net(feed: Any) -> None:
    feed.pages["/odata"] = _page([{"Id": 1}])
    with pytest.raises(IOBlockedError, match="--allow-net"):
        evaluate(_feed_call(feed.base))


# --------------------------------------------------------------------------
# Found by the claude-opus-5 review of the paging fix itself. Following a link
# the SERVER chooses introduced two ways to be wrong that one fetch could not
# have had.
# --------------------------------------------------------------------------


class _Recorder:
    """A second origin, so "same host" and "another host" are distinguishable."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []


@pytest.fixture
def other_origin() -> Any:
    state = _Recorder()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state.headers.append(dict(self.headers))
            body = json.dumps({"value": [{"id": 2}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{httpd.server_port}"  # type: ignore[attr-defined]
    yield state
    httpd.shutdown()
    thread.join(timeout=5)


def test_a_next_link_to_another_origin_does_not_carry_the_callers_headers(
    feed: Any, other_origin: Any
) -> None:
    """The credential-exfiltration hop.

    The next URL is chosen by the remote server. Carrying the caller's
    `Authorization` there hands their token to a host they never named - one
    hostile or compromised feed, one hop, token gone. Under plain
    `--allow-net` with no host allowlist the policy permits the request, so
    the policy is not what stops this.
    """
    elsewhere = f"{other_origin.base}/stolen"
    feed.pages["/odata"] = _page([{"id": 1}], elsewhere)
    rows = evaluate(
        f'OData.Feed("{feed.base}/odata", [Authorization = "Bearer dummy-token"])',
        io=NET,
    )
    assert rows == [{"id": 1}, {"id": 2}]
    assert other_origin.headers, "the second origin was never reached"
    leaked = [h for h in other_origin.headers if "Authorization" in h]
    assert leaked == [], (
        "OData.Feed sent the caller's Authorization header to an origin the "
        "feed chose, not one the caller named"
    )
    # It still asks for JSON, or the far side may answer with something else.
    assert other_origin.headers[0].get("Accept") == "application/json"


def test_a_next_link_on_the_same_origin_still_carries_them(feed: Any) -> None:
    # The fix must not break authenticated paging within one service, which
    # is the ordinary case.
    feed.pages["/odata"] = _page([{"id": 1}], f"{feed.base}/odata?page=2")
    feed.pages["/odata?page=2"] = _page([{"id": 2}])
    rows = evaluate(
        f'OData.Feed("{feed.base}/odata", [Authorization = "Bearer dummy-token"])',
        io=NET,
    )
    assert rows == [{"id": 1}, {"id": 2}]
    assert feed.hits == ["/odata", "/odata?page=2"]
    # The point of the test: the SECOND request still authenticates. Asserting
    # only the rows and the paths would pass just as happily if same-origin
    # paging had lost the header, which is the regression this guards.
    assert [h.get("Authorization") for h in feed.headers] == [
        "Bearer dummy-token",
        "Bearer dummy-token",
    ]


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        # The default port spelled out is the same origin as the default port
        # left implicit. Proxies and service-generated nextLinks write it out
        # routinely, and comparing netloc verbatim called it cross-origin -
        # which stripped the caller's Authorization and turned authenticated
        # paging into a 401 partway through a legitimate feed.
        ("https://host/a", "https://host:443/b", True),
        ("http://host/a", "http://host:80/b", True),
        ("HTTPS://Host/a", "https://host:443/b", True),
        # Everything that is genuinely a different origin still is. A
        # normalisation that swallowed these would be worse than the bug.
        ("https://host/a", "https://host:8443/b", False),
        ("https://host/a", "https://other/b", False),
        ("https://host/a", "http://host/b", False),
        # :80 is NOT the default for https, so it is a real port change.
        ("https://host/a", "https://host:80/b", False),
    ],
)
def test_the_default_port_is_the_same_origin_written_out(
    left: str, right: str, same: bool
) -> None:
    """A unit test because the live fixtures cannot reach :443/:80.

    The feed fixtures bind an ephemeral high port, so the default-port case
    is unreachable end to end without binding a privileged port. The origin
    comparison is the whole mechanism, so it is asserted directly.
    """
    from pqtools.builtins._sources import _origin

    assert (_origin(left) == _origin(right)) is same


def test_the_response_cap_bounds_the_whole_feed_not_each_page(
    feed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap was per-response, and paging multiplied it by the page ceiling.

    Before paging, a feed was one response and genuinely bounded. Following
    up to _MAX_ODATA_PAGES of them turned a hard limit into a limit times
    200, which is not a limit.
    """
    from pqtools.builtins import _sources

    monkeypatch.setattr(_sources, "_MAX_RESPONSE_BYTES", 400)
    # Each page is comfortably under 400 bytes; three of them are not.
    filler = "x" * 100
    for index in range(6):
        nxt = f"{feed.base}/odata?page={index + 1}"
        feed.pages[f"/odata?page={index}" if index else "/odata"] = _page(
            [{"id": index, "pad": filler}], nxt
        )
    with pytest.raises(EvalError, match="bytes across"):
        evaluate(f'OData.Feed("{feed.base}/odata")', io=NET)
    # It stopped early rather than reading all six.
    assert len(feed.hits) < 6


# --------------------------------------------------------------------------
# The same leak one layer down. The review named the `@odata.nextLink` hop;
# a plain HTTP 302 forwards the caller's headers the same way, through
# `_http_fetch`, which every network connector shares - so this one was
# `Web.Contents`'s too. Pre-existing, found by asking whether the sibling
# path had the defect the reviewer found in its twin.
# --------------------------------------------------------------------------


@pytest.fixture
def redirector() -> Any:
    """A server that answers 302 to wherever `state.target` points."""

    class _State:
        target = ""

    state = _State()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/same":
                self.send_response(302)
                self.send_header("Location", "/landed")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/landed":
                body = json.dumps({"value": [{"id": 9}]}).encode()
                state.landed = dict(self.headers)  # type: ignore[attr-defined]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(302)
            self.send_header("Location", state.target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{httpd.server_port}"  # type: ignore[attr-defined]
    yield state
    httpd.shutdown()
    thread.join(timeout=5)


@pytest.mark.parametrize(
    ("header", "probe"),
    [
        ('Authorization = "Bearer dummy-token"', "Authorization"),
        ('#"X-API-Key" = "dummy-key"', "X-Api-Key"),
    ],
)
def test_a_cross_origin_redirect_does_not_forward_a_credential(
    redirector: Any, other_origin: Any, header: str, probe: str
) -> None:
    """Permitting the hop and forwarding the secret are two decisions.

    `check_net` answers "may this request be made". It does not answer "may
    this server be told the caller's token" - and the redirect target is
    chosen by the server answering 302, not by the caller. A custom header is
    covered too: a credential does not have to be called `Authorization`.
    """
    redirector.target = f"{other_origin.base}/stolen"
    evaluate(f'OData.Feed("{redirector.base}/start", [{header}])', io=NET)
    assert other_origin.headers, "the redirect target was never reached"
    assert other_origin.headers[0].get(probe) is None, (
        f"{probe} was forwarded to a host chosen by the redirecting server"
    )
    assert other_origin.headers[0].get("Accept") == "application/json"


def test_a_same_origin_redirect_still_authenticates(redirector: Any) -> None:
    # Dropping credentials on every redirect would break ordinary
    # authenticated services that redirect internally.
    rows = evaluate(
        f'OData.Feed("{redirector.base}/same", [Authorization = "Bearer dummy-token"])',
        io=NET,
    )
    assert rows == [{"id": 9}]
    assert redirector.landed.get("Authorization") == "Bearer dummy-token"


def test_a_relative_next_link_resolves_against_where_the_page_LANDED(
    redirector: Any, feed: Any
) -> None:
    """`_http_fetch` returned bytes only, so the redirect was invisible.

    A page fetched from `/start` but served after a 302 to `/v2/page1`
    carries a relative next link meant to resolve against `/v2/`. Resolving
    it against the requested URL builds the wrong path. It fails loudly with
    a 404 rather than silently, which is why this was LOW - but a wrong URL
    is still a wrong URL.
    """
    feed.pages["/v2/page1"] = _page([{"id": 1}], "page2")
    feed.pages["/v2/page2"] = _page([{"id": 2}])
    redirector.target = f"{feed.base}/v2/page1"
    rows = evaluate(f'OData.Feed("{redirector.base}/start")', io=NET)
    assert rows == [{"id": 1}, {"id": 2}]
    assert feed.hits == ["/v2/page1", "/v2/page2"]
