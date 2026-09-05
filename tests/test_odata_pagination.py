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
        self.fail: set[str] = set()


@pytest.fixture
def feed() -> Any:
    state = _Feed()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state.hits.append(self.path)
            if self.path in state.fail:
                self.send_error(503)
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
