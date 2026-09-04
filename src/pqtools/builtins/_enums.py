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
Deliberately absent for that reason: RoundingMode.*, TextEncoding.*,
BinaryEncoding.*, Compression.*, CsvStyle.*, WebMethod.*. Nothing consumes them
yet either, so registering them would add risk and buy nothing.
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

BUILTINS: dict[str, Any] = {
    **_OCCURRENCE,
    **_ORDER,
    **_MISSING_FIELD,
    **_RELATIVE_POSITION,
}
