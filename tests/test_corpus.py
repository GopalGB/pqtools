import hashlib
from pathlib import Path

from pqtools.core import format_source, parse

FIXTURES = Path(__file__).parent / "fixtures" / "DataConnectors"


def test_vendor_fixture_checksums_and_parser_coverage():
    for line in (FIXTURES / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ")
        source = (FIXTURES / name).read_text(encoding="utf-8-sig")
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == expected
        assert parse(source)["tokens"]


def test_formatter_is_idempotent_for_query_fixtures():
    for name in (
        "HelloWorld.pq",
        "HelloWorld.query.pq",
        "DataWorldSwagger.query.pq",
        "github.query.pq",
    ):
        source = (FIXTURES / name).read_text(encoding="utf-8-sig")
        formatted = format_source(source)
        assert format_source(formatted) == formatted
        assert parse(formatted)["tokens"]
        protected = {"TextLiteral", "TextLiteralContent", "LineComment"}
        before = [
            (token["kind"], token["text"])
            for token in parse(source)["tokens"]
            if token["kind"] in protected
        ]
        after = [
            (token["kind"], token["text"])
            for token in parse(formatted)["tokens"]
            if token["kind"] in protected
        ]
        assert after == before


def test_m_spec_let_fixture_parses_and_formats():
    source = (FIXTURES.parent / "m-spec-let.pq").read_text(encoding="utf-8")
    formatted = format_source(source)
    assert parse(formatted)["rootKind"] == "LetExpression"
    assert format_source(formatted) == formatted
