"""M enumeration values.

These are plain values in M, not functions: `Occurrence.Last` evaluates to 1 the
same way `1` does. Registering them in a BUILTINS dict is therefore the whole
implementation - the evaluator's identifier resolution finds them exactly as it
finds a function.

Only enums whose numeric value has been VERIFIED are listed. A wrong number here
would be silent wrongness of the worst kind: `Text.PositionOf(t, s, Occurrence.Last)`
would quietly return the first match instead of the last, with no error anywhere.
So an enum whose numbering could not be confirmed is deliberately absent, and a
query using it gets an honest "unknown identifier" rather than a wrong answer.
RoundingMode.*, PercentileMode.* and TextEncoding.* were absent for that reason
until 0.10.0. Both halves of the reason have since changed: the numbering is
verified against their own `*-type` reference pages, and they are consumed -
Text.ToBinary/Text.FromBinary/Lines.FromBinary already take a code page, and
Number.Round now takes a rounding mode. They were found missing by running
Microsoft's own worked examples, which is the only place a real query's
vocabulary shows up.

QuoteStyle.*, ExtraValues.* and CsvStyle.* used to be registered next to
their consumer in _table_shape.py as OPAQUE self-naming strings, on the
stated grounds that their numbering was unconfirmed. It was not unconfirmed -
it was unfetched. `quotestyle-type`, `extravalues-type` and `csvstyle-type`
are all real pages carrying the usual Name/Value table, and they were missing
from the offline cache only because nothing had ever asked for them. The cost
of the guess was real: `QuoteStyle.Csv` IS the number 1 in M, so
`Splitter.SplitTextByDelimiter(",", 1)` is a legal call that this refused.

The lesson generalises - "the number is unverifiable" is a claim that itself
needs verifying. `tests/test_documented_enum_values.py` now checks every
number here against its own `*-type` page.

Still deliberately absent: WebMethod.*, BufferMode.* - no `*-type` page
exists for either (both 404), and nothing consumes them.
"""

from __future__ import annotations

from typing import Any

# Occurrence.Type - verified against Microsoft Learn's Occurrence.Type page.
# Consumed by Text.PositionOf / Text.PositionOfAny / List.PositionOf.
_OCCURRENCE = {
    "Occurrence.First": 0,
    "Occurrence.Last": 1,
    "Occurrence.All": 2,
}

# Order.Type - moved here from _table.py, which had it as a special case that
# evaluate.py imported directly. Two resolution mechanisms for one concept meant
# every new enum family looked unwireable to anyone reading only the special case
# (it did: an implementer reported Occurrence.* as impossible to add). One
# mechanism now: register the value, the resolver finds it.
_ORDER = {
    "Order.Ascending": 0,
    "Order.Descending": 1,
}

# MissingField.Type - the numbering the Record.* implementations already expect
# and document.
_MISSING_FIELD = {
    "MissingField.Error": 0,
    "MissingField.Ignore": 1,
    "MissingField.UseNull": 2,
}

# RelativePosition.Type. Registered so the VALUE resolves; the functions that
# take it (Text.BeforeDelimiter's list form and friends) still raise
# UnsupportedError naming the option, which is an honest refusal rather than a
# silently ignored argument.
_RELATIVE_POSITION = {
    "RelativePosition.FromStart": 0,
    "RelativePosition.FromEnd": 1,
}


# RoundingMode.Type - verified against Microsoft Learn's RoundingMode.Type page
# ("Allowed values" table). Consumed by Number.Round's third argument. These
# select the tie-break direction ONLY; a non-tie rounds the same way whatever
# the mode.
_ROUNDING_MODE = {
    "RoundingMode.Up": 0,
    "RoundingMode.Down": 1,
    "RoundingMode.AwayFromZero": 2,
    "RoundingMode.TowardZero": 3,
    "RoundingMode.ToEven": 4,
}

# PercentileMode.Type - verified against its own page. Registered so a query
# naming one reads as a real value; List.Percentile still refuses the option by
# name, because the four interpolation methods give different answers and
# picking one silently would be the wrong kind of helpful.
_PERCENTILE_MODE = {
    "PercentileMode.ExcelInc": 1,
    "PercentileMode.ExcelExc": 2,
    "PercentileMode.SqlDisc": 3,
    "PercentileMode.SqlCont": 4,
}

# TextEncoding.Type - verified against its own page. The values ARE Windows
# code page numbers, which Text.ToBinary/Text.FromBinary/Lines.FromBinary
# already accept, so these names work the moment they resolve. Utf16 and
# Unicode are the same code page (1200); that duplication is Microsoft's.
_TEXT_ENCODING = {
    "TextEncoding.Utf16": 1200,
    "TextEncoding.Unicode": 1200,
    "TextEncoding.BigEndianUnicode": 1201,
    "TextEncoding.Windows": 1252,
    "TextEncoding.Ascii": 20127,
    "TextEncoding.Utf8": 65001,
}

# QuoteStyle.Type / ExtraValues.Type / CsvStyle.Type - verified against their
# own pages, which is how they stopped being self-naming strings. Note that
# ExtraValues is NOT in menu order: List is 0, Error is 1, Ignore is 2.
_QUOTE_STYLE = {
    "QuoteStyle.None": 0,
    "QuoteStyle.Csv": 1,
}

_EXTRA_VALUES = {
    "ExtraValues.List": 0,
    "ExtraValues.Error": 1,
    "ExtraValues.Ignore": 2,
}

_CSV_STYLE = {
    "CsvStyle.QuoteAfterDelimiter": 0,
    "CsvStyle.QuoteAlways": 1,
}

BUILTINS: dict[str, Any] = {
    **_OCCURRENCE,
    **_ORDER,
    **_MISSING_FIELD,
    **_RELATIVE_POSITION,
    **_ROUNDING_MODE,
    **_PERCENTILE_MODE,
    **_TEXT_ENCODING,
    **_QUOTE_STYLE,
    **_EXTRA_VALUES,
    **_CSV_STYLE,
}
