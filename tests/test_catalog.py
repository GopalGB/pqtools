"""Every name pqtools answers to must be a name Power Query has.

This suite exists because of a defect class nothing else here could catch.

pqtools shipped `Uri.UnescapeDataString` and `Table.SelectDuplicates`. Both
had implementations, tests, and README entries. Neither exists in Power Query
M - Microsoft documents four Uri functions and that is not one of them, and
`table-selectduplicates` is a 404 while `table-selectrows` is a 200.

A MISSING function is an honest error: the query fails here and the user
learns something true. An INVENTED function is worse than either: the query
runs green in pqtools and then fails in Power Query, which is the exact
inverse of this package's only promise. No amount of testing the function's
behaviour finds this, because the behaviour was fine. Only comparing the
registry against Microsoft's own reference finds it.

The other half of the file guards the error message. A documented function we
have not implemented must say so; a typo must still read as a typo. Collapsing
those two into one "unknown identifier" is what let `Salesforce.Data` look
like a misspelling.
"""

from __future__ import annotations

import pytest

from pqtools import UnsupportedError, evaluate
from pqtools.catalog import DOCUMENTED, REASONS, explain
from pqtools.evaluate import BUILTINS

# Real M names that are values rather than functions, so they appear on their
# own `*-type` reference pages instead of in a category's function table. Each
# group is genuinely part of the language - several are documented inline on
# the Table and List function pages (for example "Order.Ascending = 0" and
# "MissingField.Error = 0" appear there verbatim).
#
# This list is the ONLY sanctioned way to register a name Microsoft's function
# tables do not list. Adding to it requires a reference page that shows the
# name. It is not a place to park a function you could not find.
ENUM_AND_TYPE_VALUES = {
    # Sort order, occurrence, extra values, missing fields - all documented
    # inline on the table-functions and list-functions pages.
    "Order.Ascending",
    "Order.Descending",
    "Occurrence.First",
    "Occurrence.Last",
    "Occurrence.All",
    "ExtraValues.List",
    "ExtraValues.Error",
    "ExtraValues.Ignore",
    "MissingField.Error",
    "MissingField.Ignore",
    "MissingField.UseNull",
    # Join and group behaviour.
    "JoinKind.Inner",
    "JoinKind.LeftOuter",
    "JoinKind.RightOuter",
    "JoinKind.FullOuter",
    "JoinKind.LeftAnti",
    "JoinKind.RightAnti",
    # JoinKind.Type documents eight members; these two were absent.
    "JoinKind.LeftSemi",
    "JoinKind.RightSemi",
    "GroupKind.Global",
    "GroupKind.Local",
    # Csv.Document / Text options.
    "QuoteStyle.Csv",
    "QuoteStyle.None",
    "RelativePosition.FromStart",
    "RelativePosition.FromEnd",
    "BinaryEncoding.Base64",
    "BinaryEncoding.Hex",
    "Compression.None",
    "Compression.GZip",
    "Compression.Deflate",
    "Precision.Double",
    "Precision.Decimal",
    # Rank behaviour for Table.AddRankColumn (learn.microsoft.com/en-us/
    # powerquery-m/rankkind-type returns 200; a made-up "bogus-type" 404s,
    # which is the control that makes that check mean something).
    "RankKind.Competition",
    "RankKind.Dense",
    "RankKind.Ordinal",
    # Rounding, percentile and text-encoding modes. Each has its own
    # `<name>-type` reference page carrying an "Allowed values" table with
    # the numbers; `Number.PI` has its own page too and is a plain constant.
    # All were found missing by running Microsoft's own worked examples.
    # Number.PI is a CONSTANT, so it is absent from the number-functions
    # table the inventory is scraped from, but learn.microsoft.com/en-us/
    # powerquery-m/number-pi is a 200 and describes it as "a constant that
    # represents 3.1415926535897932".
    "Number.PI",
    "RoundingMode.Up",
    "RoundingMode.Down",
    "RoundingMode.AwayFromZero",
    "RoundingMode.TowardZero",
    "RoundingMode.ToEven",
    "PercentileMode.ExcelInc",
    "PercentileMode.ExcelExc",
    "PercentileMode.SqlDisc",
    "PercentileMode.SqlCont",
    "TextEncoding.Utf16",
    "TextEncoding.Unicode",
    "TextEncoding.BigEndianUnicode",
    "TextEncoding.Windows",
    "TextEncoding.Ascii",
    "TextEncoding.Utf8",
    # Trace levels, byte order, and binary-occurrence kinds. Each has its own
    # `<name>-type` page carrying an "Allowed values" table listing exactly
    # these members (tracelevel-type, byteorder-type, binaryoccurrence-type
    # all return 200; a made-up "bogus-nonexistent-type" 404s, which is the
    # control that makes those three checks mean anything). Diagnostics.Trace
    # and the BinaryFormat.* combinators are their only consumers, and
    # Microsoft's own worked examples for BinaryFormat.Group use
    # BinaryOccurrence.* by name - an example that cannot resolve its own
    # identifiers is the one thing the doc-example gate never tolerates.
    "TraceLevel.Critical",
    "TraceLevel.Error",
    "TraceLevel.Warning",
    "TraceLevel.Information",
    "TraceLevel.Verbose",
    "ByteOrder.LittleEndian",
    "ByteOrder.BigEndian",
    "BinaryOccurrence.Optional",
    "BinaryOccurrence.Required",
    "BinaryOccurrence.Repeating",
    # Day-of-week constants.
    "Day.Monday",
    "Day.Tuesday",
    "Day.Wednesday",
    "Day.Thursday",
    "Day.Friday",
    "Day.Saturday",
    "Day.Sunday",
    # Ascribable primitive types (`type number`, Int64.Type, ...).
    "Any.Type",
    "Text.Type",
    "Number.Type",
    "Logical.Type",
    "Date.Type",
    "DateTime.Type",
    "Byte.Type",
    "Currency.Type",
    "Decimal.Type",
    "Double.Type",
    "Single.Type",
    "Percentage.Type",
    "Int8.Type",
    "Int16.Type",
    "Int32.Type",
    "Int64.Type",
}


def test_every_registered_builtin_is_a_real_power_query_name() -> None:
    """The gate. An invented function passes every other test in this repo."""
    invented = sorted(
        name
        for name in BUILTINS
        if "." in name and name not in DOCUMENTED and name not in ENUM_AND_TYPE_VALUES
    )
    assert invented == [], (
        f"{len(invented)} registered name(s) are not in Microsoft's M reference "
        f"and are not sanctioned enum/type values: {invented}. A query using one "
        "runs here and fails in Power Query. Remove it, or - if it really is "
        "documented - add it to ENUM_AND_TYPE_VALUES with the page that shows it."
    )


def test_the_two_functions_that_were_invented_are_gone() -> None:
    """Named explicitly so a well-meaning revert is caught immediately."""
    assert "Uri.UnescapeDataString" not in BUILTINS
    assert "Table.SelectDuplicates" not in BUILTINS


def test_the_catalog_is_populated() -> None:
    # An empty catalog would silently disarm every message below while every
    # other test still passed.
    assert len(DOCUMENTED) > 600
    assert set(DOCUMENTED.values()) <= set(REASONS)


# --------------------------------------------------------------------------
# The error message
# --------------------------------------------------------------------------


def test_a_typo_still_reads_as_a_typo() -> None:
    """The distinction the catalog exists to preserve."""
    for typo in ("Tabel.RowCount", "Text.Uppercase", "Foo.Bar"):
        assert explain(typo) is None
        with pytest.raises(UnsupportedError, match="unknown identifier"):
            evaluate(f"{typo}(1)")


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("Salesforce.Data()", "data-source connector"),
        ("AzureStorage.Blobs(1)", "data-source connector"),
        ("Cube.Measures(1)", "Power BI data model"),
        ("Comparer.FromCulture(1)", "culture"),
    ],
)
def test_a_documented_function_explains_itself(call: str, expected: str) -> None:
    """ "unknown identifier: Salesforce.Data" reads as a misspelling.

    It is a real function this package does not run, and the error has to say
    which of those two things is true - they have completely different fixes.
    """
    with pytest.raises(UnsupportedError, match=expected):
        evaluate(call)


def test_the_connector_message_names_the_way_forward() -> None:
    # An error that only says "no" makes the reader go and read our source.
    with pytest.raises(UnsupportedError, match="--bind"):
        evaluate('Teradata.Database("host")')


def test_an_implemented_function_never_reaches_the_catalog() -> None:
    """Self-maintaining: implementing a function retires its catalog entry.

    The previous hand-kept connector list had drifted to five names while
    Microsoft documented eighty-five. Nothing has to be deleted by hand here.
    """
    assert "Table.RowCount" in DOCUMENTED  # catalogued...
    assert evaluate('Table.RowCount(#table({"a"},{{1}}))') == 1  # ...and it runs


# --------------------------------------------------------------------------
# The documented coverage number
# --------------------------------------------------------------------------


@pytest.mark.parametrize("document", ["README.md", "llms.txt"])
def test_the_stated_coverage_is_the_real_coverage(document: str) -> None:
    """Both documents must state a number that is computed, not remembered.

    llms.txt spent two releases telling AI assistants that connectors shipped
    in 0.8.0 were unsupported, because its capability prose was hand-edited
    and the hand forgot. A number nobody recomputes is a number that lies.

    Fix a failure here by running `python scripts/sync_builtin_list.py`,
    never by editing the number.
    """
    from pathlib import Path

    covered = sum(1 for name in DOCUMENTED if name in BUILTINS)
    text = (Path(__file__).parent.parent / document).read_text(encoding="utf-8")
    assert f"**{covered} of the {len(DOCUMENTED)}**" in text, (
        f"{document} states a stale coverage figure; the registry now covers "
        f"{covered} of {len(DOCUMENTED)}. Run scripts/sync_builtin_list.py."
    )
