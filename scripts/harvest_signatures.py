"""Harvest every documented Syntax block into `tests/fixtures/m-signatures.json`.

The defect class this exists to close: an INVENTED SIGNATURE. The function
name is real, so the catalog gate cannot see it. The behaviour is right for
the arguments that are accepted, so no behavioural test sees it. It shows up
only as "the call Microsoft's own page documents does not work here" - which
is exactly what a user hits first.

Two were found by hand (`Duration.FromText`, `Oracle.Database`), then thirteen
more by asking the question of all 638 builtins at once. Asking it by hand is
how the next thirteen get missed, so the answer is checked in as a fixture and
the question is asked by a test on every run.

The fixture records, per documented function:

    arity        - (required, total) from the `optional` keyword
    parameters   - name, type text, optional flag, nullable flag
    returns      - the declared return type text

Regenerate after refreshing the offline doc cache:

    .venv/bin/python scripts/harvest_signatures.py

It reads `/tmp/pqtools-doc-cache/*.html` when that cache is present and
otherwise leaves the fixture untouched, so a machine without the cache can
still run the test suite - the fixture is the source of truth, not the cache.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path("/tmp/pqtools-doc-cache")
FIXTURE = ROOT / "tests" / "fixtures" / "m-signatures.json"

sys.path.insert(0, str(ROOT / "src"))

from pqtools.evaluate import BUILTINS  # noqa: E402


def _slug(name: str) -> str:
    return name.lower().replace(".", "-").lstrip("#")


def _page_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def _balanced(text: str, start: int) -> str | None:
    """The argument list at `start` (which indexes the opening paren)."""
    depth = 0
    for i in range(start, min(len(text), start + 4000)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
    return None


def _split_params(inside: str) -> list[str]:
    params: list[str] = []
    depth = 0
    current = ""
    for ch in inside:
        if ch == "," and depth == 0:
            params.append(current)
            current = ""
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        current += ch
    params.append(current)
    return [p.strip() for p in params if p.strip()]


def _parse_parameter(raw: str) -> dict[str, object]:
    optional = raw.lower().startswith("optional")
    if optional:
        raw = raw[len("optional") :].strip()
    name, _, type_text = raw.partition(" as ")
    type_text = type_text.strip()
    return {
        "name": name.strip(),
        "type": type_text,
        "optional": optional,
        "nullable": type_text.lower().startswith("nullable ")
        or type_text.lower() in ("any", "nullable any"),
    }


def signature(name: str) -> dict[str, object] | None:
    page = CACHE / f"{_slug(name)}.html"
    if not page.exists():
        return None
    text = _page_text(page)
    match = re.search(r"Syntax\s+" + re.escape(name) + r"\s*\(", text)
    if not match:
        return None
    inside = _balanced(text, match.end() - 1)
    if inside is None:
        return None
    # The return type is what follows the closing paren, up to the About
    # section: `) as nullable text About ...`.
    tail = text[match.end() - 1 + len(inside) + 2 :].strip()
    returns = ""
    if tail.lower().startswith("as "):
        returns = tail[3:].split(" About")[0].strip()
    params = [_parse_parameter(p) for p in _split_params(inside.strip())]
    required = sum(1 for p in params if not p["optional"])
    return {
        "arity": [required, len(params)],
        "parameters": params,
        "returns": returns,
    }


ENUM_FIXTURE = ROOT / "tests" / "fixtures" / "m-enum-values.json"

# A row of the Name/Value/Description table on a `<family>-type` page. The
# cells wrap the name in <code> on some pages and <strong> on others (and
# nothing at all on a third), so the tags are stripped rather than matched -
# the first parser matched only <code> and silently lost RelativePosition.
_ROW = re.compile(r"<tr>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*\.[A-Za-z0-9]+$")


def enum_values(family: str) -> dict[str, int]:
    """The Name/Value table on a `<family>-type` page.

    These pages were absent from the offline cache for a long time, and their
    absence was recorded in `_enums.py` as "numbering unconfirmed" - a claim
    about the world that was really a claim about the cache. `QuoteStyle.Csv`
    is 1, and refusing to believe it made a legal M call fail.
    """
    page = CACHE / f"{family.lower()}-type.html"
    if not page.exists():
        return {}
    raw = page.read_text(encoding="utf-8", errors="replace")
    found: dict[str, int] = {}
    for row in _ROW.findall(raw):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in _CELL.findall(row)
        ]
        if len(cells) < 2 or not _NAME.match(cells[0]):
            continue
        try:
            found[cells[0]] = int(cells[1])
        except ValueError:
            continue
    return found


def harvest_enums() -> dict[str, int]:
    families = sorted(
        {
            name.split(".")[0]
            for name in BUILTINS
            if "." in name and not callable(BUILTINS[name])
        }
    )
    found: dict[str, int] = {}
    for family in families:
        found.update(enum_values(family))
    return found


def main() -> int:
    if not CACHE.is_dir():
        print(f"doc cache absent ({CACHE}); fixture left untouched")
        return 0
    harvested: dict[str, object] = {}
    for name in sorted(BUILTINS):
        parsed = signature(name)
        if parsed is not None:
            harvested[name] = parsed
    FIXTURE.write_text(
        json.dumps(harvested, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"{len(harvested)} signatures -> {FIXTURE.relative_to(ROOT)}")
    enums = harvest_enums()
    ENUM_FIXTURE.write_text(
        json.dumps(enums, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"{len(enums)} enum values -> {ENUM_FIXTURE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
