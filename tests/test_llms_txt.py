"""llms.txt is the file an AI assistant quotes; it must not be able to drift.

The builtin count and the coverage sentence are already computed rather than
remembered. This extends that discipline to the parts of llms.txt an agent
ACTS on: the error codes it branches on, and the credential variables it tells
a human to set. Both are read from the code, so an error code added without
documenting it - or documented without existing - fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LLMS = (_ROOT / "llms.txt").read_text(encoding="utf-8")
_SRC = _ROOT / "src" / "pqtools"


def _codes_in_code() -> set[str]:
    codes: set[str] = set()
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        codes.update(re.findall(r'code = "([A-Z_]+)"', text))
        # main()'s fallback for a raw OSError is a literal, not a class.
        codes.update(re.findall(r'"code", "([A-Z_]+)"', text))
    return codes


def _codes_in_document() -> set[str]:
    section = _LLMS.split("## Error codes", 1)[1].split("\n## ", 1)[0]
    # The first cell of each table row, and only that: a prefix match on any
    # backticked token also caught `MQUERY_NODE`, the environment variable
    # named in the NODE_ERROR row, and reported it as an invented code.
    return set(re.findall(r"^\| `([A-Z_]+)` \|", section, re.MULTILINE))


def test_every_error_code_the_code_can_raise_is_documented() -> None:
    missing = _codes_in_code() - _codes_in_document()
    assert not missing, f"llms.txt does not document these codes: {sorted(missing)}"


def test_every_documented_error_code_exists() -> None:
    invented = _codes_in_document() - _codes_in_code()
    assert not invented, f"llms.txt names codes nothing raises: {sorted(invented)}"


def test_llms_txt_names_the_credential_variables_the_code_reads() -> None:
    # The README has the same test; llms.txt is quoted more widely.
    source = (_SRC / "builtins" / "_sources.py").read_text(encoding="utf-8")
    prefixes = set(re.findall(r'"(PQTOOLS_[A-Z]+)"', source))
    assert prefixes, "no credential prefixes found in the connector source"
    for prefix in prefixes:
        assert f"{prefix}_" in _LLMS, f"llms.txt never names {prefix}_USER/_PASSWORD"


def test_llms_txt_states_the_json_error_shape_main_emits() -> None:
    cli = (_SRC / "cli.py").read_text(encoding="utf-8")
    assert '"code": code' in cli and '"message": str(error)' in cli
    assert '{"code": "...", "message": "..."}' in _LLMS
