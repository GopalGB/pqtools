import re
from pathlib import Path

_RULES_FILE = Path(__file__).resolve().parents[1] / "semgrep-rules.yaml"
_PATTERN_LINE = re.compile(r"^\s*pattern-regex:\s*'(.*)'\s*$", re.MULTILINE)


def test_no_hardcoded_bearer_regex_matches_secrets_but_not_the_fstring():
    text = _RULES_FILE.read_text(encoding="utf-8")
    match = _PATTERN_LINE.search(text)
    assert match is not None
    pattern = match.group(1)
    assert re.search(pattern, "Bearer abcdefghijklmnop1234567890")
    assert not re.search(pattern, 'f"Bearer {token}"')
