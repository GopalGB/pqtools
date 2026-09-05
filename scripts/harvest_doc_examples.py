"""Harvest every worked example from Microsoft's M function reference.

Why this exists: four separate releases shipped a bug that a thousand green
tests could not see - `#table`, `#(lf)`, `Source{[Item=...]}`, and an operator
layer that only handled numbers. Every one was found by running real Power
Query M, and every one survived the suite because the suite is written by the
people who wrote the code, using the idioms those people reach for.

The fix is a corpus nobody here authored. Microsoft's reference pages carry
several hundred worked examples in a rigidly regular shape:

    <p><strong>Usage</strong></p>
    <pre><code class="lang-powerquery-m"> ...M... </code></pre>
    <p><strong>Output</strong></p>
    <pre><code class="lang-powerquery-m"> ...M... </code></pre>

That is authoritative M with an authoritative expected answer. This script
collects the pairs into `tests/fixtures/doc-examples.json` so the suite runs
them offline and deterministically; `tests/test_doc_examples.py` is the gate.

    python scripts/harvest_doc_examples.py            # incremental, cached
    python scripts/harvest_doc_examples.py --refresh  # ignore the HTML cache

Pages are cached on disk, so a re-run costs nothing and the site is fetched
once per name.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "doc-examples.json"
CACHE = Path("/tmp/pqtools-doc-cache")
BASE = "https://learn.microsoft.com/en-us/powerquery-m/"

# Usage/Output pairs, in page order. `.*?` across the block is deliberate:
# some pages put the heading text in <strong>, others in <b>.
_BLOCK = re.compile(
    r"<(?:strong|b)>Usage</(?:strong|b)>.*?"
    r'<pre><code class="lang-powerquery-m">(?P<usage>.*?)</code></pre>'
    r"(?:.*?<(?:strong|b)>Output</(?:strong|b)>.*?"
    r'<pre><code class="lang-powerquery-m">(?P<output>.*?)</code></pre>)?',
    re.DOTALL,
)
# Where the next example starts, so a missing Output cannot swallow the
# following example's code block and pair the wrong two together.
_SPLIT = re.compile(r'<h2 id="example[^"]*">', re.IGNORECASE)


def slug_for(name: str) -> str:
    return name.lower().replace(".", "-").replace("#", "")


def fetch(slug: str, refresh: bool) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{slug}.html"
    if cached.exists() and not refresh:
        return cached.read_text(encoding="utf-8", errors="replace")
    request = urllib.request.Request(
        BASE + slug, headers={"User-Agent": "pqtools-doc-harvester"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        # A 404 is information, not a failure: it means the name is not in
        # Microsoft's reference. Record it so the caller can act on it.
        return None if error.code == 404 else ""
    except (urllib.error.URLError, TimeoutError):
        return ""
    cached.write_text(body, encoding="utf-8")
    return body


def examples_in(page: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for section in _SPLIT.split(page)[1:] or [page]:
        match = _BLOCK.search(section)
        if not match:
            continue
        usage = html.unescape(match.group("usage")).strip()
        raw_output = match.group("output")
        output = html.unescape(raw_output).strip() if raw_output else ""
        if usage:
            found.append({"usage": usage, "output": output})
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--jobs", type=int, default=6)
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from pqtools.catalog import DOCUMENTED  # noqa: PLC0415

    names = sorted(DOCUMENTED)
    harvested: dict[str, list[dict[str, str]]] = {}
    missing: list[str] = []
    failed: list[str] = []

    def work(name: str) -> None:
        page = fetch(slug_for(name), args.refresh)
        if page is None:
            missing.append(name)
            return
        if not page:
            failed.append(name)
            return
        found = examples_in(page)
        if found:
            harvested[name] = found

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(work, names))

    if failed:
        # A network blip must not silently shrink the corpus - that would
        # quietly disarm the gate this file exists to arm.
        print(
            f"{len(failed)} page(s) could not be fetched: {failed[:10]}",
            file=sys.stderr,
        )
        return 1

    total = sum(len(v) for v in harvested.values())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"source": BASE, "functions": harvested},
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{total} worked examples from {len(harvested)} of {len(names)} pages")
    if missing:
        print(f"{len(missing)} name(s) 404 - not in the reference: {missing[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
