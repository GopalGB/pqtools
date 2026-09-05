"""Regenerate ``src/pqtools/catalog.py``'s DOCUMENTED table.

Reads the grounded inventory at ``.planning/m-inventory.md`` - which was built
by fetching every Microsoft Learn category page - and writes the name-to-reason
mapping the evaluator uses to explain a documented-but-unimplemented function.

Run it after refreshing the inventory. It is deliberately a generator rather
than a hand-maintained list: the previous hand-maintained set of connector
names had drifted to five entries while Microsoft documented eighty-five, and
that drift is what let `Salesforce.Data` report itself as a typo.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / ".planning" / "m-inventory.md"
CATALOG = ROOT / "src" / "pqtools" / "catalog.py"

# Reasons that are more specific than the "not implemented yet" default.
# Matched most-specific-first: exact name, then prefix, then category.
EXACT: dict[str, str] = {
    "Comparer.FromCulture": "culture",
    "Table.View": "folding",
    "Table.ViewError": "folding",
    "Table.ViewFunction": "folding",
    "Table.StopFolding": "folding",
    "Value.NativeQuery": "folding",
    "Value.Optimize": "folding",
    "ItemExpression.From": "engine",
    "RowExpression.From": "engine",
    "RowExpression.Column": "engine",
    "Tables.GetRelationships": "model",
    "Excel.CurrentWorkbook": "engine",
    # Parseable with the standard library, but the returned table's columns
    # are undocumented - "a hierarchical table", "a nested collection of
    # flattened tables", with no worked output on either page. A query does
    # Source{0}[Value]; invent the column names and it reads the wrong field
    # without erroring.
    "Xml.Document": "shape",
    "Xml.Tables": "shape",
    "Html.Table": "shape",
    "Pdf.Tables": "shape",
}

PREFIX: list[tuple[str, str]] = [
    ("Cube.", "model"),
    ("Table.Fuzzy", "fuzzy"),
    ("Table.AddFuzzy", "fuzzy"),
    ("Identity.", "engine"),
    ("IdentityProvider.", "engine"),
    ("AccessControlEntry.", "engine"),
]

# Everything still missing from these categories needs an outside system.
CONNECTOR_CATEGORIES = {"accessing-data-functions"}


def reason_for(name: str, category: str) -> str:
    if name in EXACT:
        return EXACT[name]
    for prefix, reason in PREFIX:
        if name.startswith(prefix):
            return reason
    if category in CONNECTOR_CATEGORIES:
        return "connector"
    return "notyet"


def parse_inventory(text: str) -> dict[str, str]:
    """Collect every documented name from the inventory, with its category.

    Both the implemented and the missing names are recorded. An implemented
    one costs nothing - `explain()` is only consulted for names absent from
    BUILTINS - and recording it means the catalog stays correct when a
    function is later removed or renamed.
    """
    catalog: dict[str, str] = {}
    category = ""
    for line in text.splitlines():
        heading = re.match(r"^## (\S+)", line)
        if heading:
            category = heading.group(1)
            continue
        listed = re.match(r"^(?:ALREADY IMPLEMENTED|MISSING) \(\d+\):\s*(.+)$", line)
        if not listed or not category:
            continue
        for raw in listed.group(1).split(","):
            name = raw.strip()
            # Real M names only: a namespace, a dot, a member - plus the
            # `#table`-style hash intrinsics.
            if re.fullmatch(r"#?[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9]*", name):
                catalog[name] = reason_for(name, category)
    return catalog


def main() -> int:
    if not INVENTORY.exists():
        print(f"missing inventory: {INVENTORY}", file=sys.stderr)
        return 1
    catalog = parse_inventory(INVENTORY.read_text(encoding="utf-8"))
    if len(catalog) < 400:
        # A parser change that silently matched nothing would quietly disarm
        # every good error message this file exists to produce.
        print(f"only {len(catalog)} names parsed; expected 400+", file=sys.stderr)
        return 1

    rendered = "".join(
        f"    {name!r}: {reason!r},\n" for name, reason in sorted(catalog.items())
    )
    source = CATALOG.read_text(encoding="utf-8")
    updated = re.sub(
        r"DOCUMENTED: dict\[str, str\] = \{.*?\}",
        "DOCUMENTED: dict[str, str] = {\n" + rendered + "}",
        source,
        count=1,
        flags=re.DOTALL,
    )
    if updated == source:
        print("could not find the DOCUMENTED table to replace", file=sys.stderr)
        return 1
    CATALOG.write_text(updated, encoding="utf-8")

    counts: dict[str, int] = {}
    for reason in catalog.values():
        counts[reason] = counts.get(reason, 0) + 1
    print(f"catalogued {len(catalog)} documented M functions")
    for reason, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
