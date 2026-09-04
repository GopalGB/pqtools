"""Regenerate the builtin list and counts in README.md and llms.txt.

README claims the list "is generated from pqtools.evaluate.BUILTINS". That was
aspirational: the list was hand-maintained, drifted from 49 to 270 names behind
the code, and only a test caught it. This script makes the claim true.

Run it after adding a builtin:

    .venv/bin/python scripts/sync_builtin_list.py

`tests/test_readme_builtins.py` fails if it has not been run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pqtools.evaluate import BUILTINS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WIDTH = 76

# Grouped the way a reader looks things up - by M namespace, in the order the
# families appear in a real query, with the hash-literals last because they
# are syntax rather than functions.
ORDER = [
    "Text",
    "Number",
    "Logical",
    "List",
    "Record",
    "Table",
    "Type",
    "Value",
    "Date",
    "DateTime",
    "DateTimeZone",
    "Time",
    "Duration",
    "Csv",
    "Json",
    "Xml",
    "Lines",
    "File",
    "Folder",
    "Excel",
    "Web",
    "OData",
    "Sql",
    "Odbc",
    "PostgreSQL",
    "MySQL",
    "Oracle",
    "Uri",
    "Binary",
    "BinaryEncoding",
    "Compression",
    "Character",
    "Guid",
    "Splitter",
    "Combiner",
    "Replacer",
    "Comparer",
    "Precision",
    "Order",
    "JoinKind",
    "JoinAlgorithm",
    "MissingField",
    "Occurrence",
    "RoundingMode",
    "ExtraValues",
    "QuoteStyle",
    "TextEncoding",
    "Percentile",
    "GroupKind",
    "Int8",
    "Int16",
    "Int32",
    "Int64",
    "Single",
    "Double",
    "Decimal",
    "Currency",
    "Byte",
    "Any",
    "None",
    "Function",
    "Expression",
]


def _wrap(names: list[str]) -> list[str]:
    lines: list[str] = []
    current = ""
    for name in names:
        candidate = f"{current} {name}".strip()
        if len(candidate) > WIDTH and current:
            lines.append(current)
            current = name
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _block() -> str:
    remaining = set(BUILTINS)
    out: list[str] = []
    for namespace in ORDER:
        group = sorted(n for n in remaining if n.split(".")[0] == namespace)
        if group:
            out.extend(_wrap(group))
            remaining -= set(group)
    leftover = sorted(n for n in remaining if not n.startswith("#"))
    if leftover:
        out.extend(_wrap(leftover))
        remaining -= set(leftover)
    hashes = sorted(remaining)
    if hashes:
        out.extend(_wrap(hashes))
    return "\n".join(out)


def main() -> int:
    total = len(BUILTINS)
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")

    marker = "and these "
    start = text.index(marker)
    fence = text.index("```", start) + 3
    end = text.index("```", fence)
    text = text[: fence + 1] + _block() + "\n" + text[end:]

    text = re.sub(r"and these \d+ builtins", f"and these {total} builtins", text)
    text = re.sub(
        r"a fair description: \d+ M builtins",
        f"a fair description: {total} M builtins",
        text,
    )
    readme.write_text(text, encoding="utf-8")

    llms = ROOT / "llms.txt"
    llms.write_text(
        re.sub(r"\d+ M builtins", f"{total} M builtins", llms.read_text("utf-8")),
        encoding="utf-8",
    )
    print(f"synced {total} builtins into README.md and llms.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
