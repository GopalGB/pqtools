"""Tests for `Text.Format` (new), and the `Text.From`/`Number.From` temporal
and binary conversions added alongside it in
`src/pqtools/builtins/_text.py` / `src/pqtools/builtins/_number.py`.

Every expected string or number below is either copied verbatim from
Microsoft's own worked examples (learn.microsoft.com/en-us/powerquery-m/
text-format, text-from, number-from - fetched during this task) or derived
by running this package's own `*.ToText`/`Date.From` through `evaluate()`
and cross-checking the result, never hand-typed from memory. Where a
behaviour is a documented CHOICE rather than a fact from the reference (the
page does not say), the test and the implementation comment it next to say
so.
"""

from __future__ import annotations

import pytest

from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Text.Format - list arguments (#{N})
# --------------------------------------------------------------------------


def test_text_format_list_example_1():
    # Verbatim Example 1 from learn.microsoft.com/en-us/powerquery-m/text-format.
    assert (
        evaluate('Text.Format("#{0}, #{1}, and #{2}.", {17, 7, 22})')
        == "17, 7, and 22."
    )


def test_text_format_list_reuses_the_same_index_twice():
    # Nothing in the docs forbids repeating an index; a naive one-pass
    # consuming iterator over the list (instead of indexing by number)
    # would break this.
    assert evaluate('Text.Format("#{0}-#{0}", {"x"})') == "x-x"


def test_text_format_list_out_of_range_index_fails_loudly():
    # Microsoft's page does not say what an out-of-range index does. This
    # package refuses by name instead of silently emitting "" (the naive
    # str.format-style answer) - see _text.py's _text_format for the full
    # reasoning. A regression back to a silent empty-string substitution
    # would corrupt a user's formatted text with no error raised anywhere.
    with pytest.raises(EvalError, match="index 5 is out of range"):
        evaluate('Text.Format("#{5}", {1, 2, 3})')


def test_text_format_negative_and_non_numeric_tokens_are_not_placeholders():
    # `#{-1}` and `#{x}` do not match the digit-only placeholder pattern at
    # all, so they pass through as literal text rather than erroring or
    # substituting - there is no argument to substitute a non-numeric
    # index against, and the docs never show this shape.
    assert evaluate('Text.Format("#{-1} #{x}", {1})') == "#{-1} #{x}"


# --------------------------------------------------------------------------
# Text.Format - record arguments (#[name])
# --------------------------------------------------------------------------


def test_text_format_record_example_2_verbatim():
    # Verbatim Example 2 from learn.microsoft.com/en-us/powerquery-m/
    # text-format - this is the acceptance test the task brief names
    # explicitly. It exercises three things at once: record-field
    # placeholders, a text value passed through unchanged, and two
    # temporal values (#date, #duration) rendered exactly as this
    # package's own Date.ToText/Duration.ToText already render them.
    query = """
    Text.Format(
        "The time for the #[distance] km run held in #[city] on #[date] was "
        & "#[duration].",
        [city = "Seattle", date = #date(2015, 3, 10),
         duration = #duration(0, 0, 54, 40), distance = 10],
        "en-US"
    )
    """
    assert evaluate(query) == (
        "The time for the 10 km run held in Seattle on 3/10/2015 was 00:54:40."
    )


def test_text_format_record_missing_field_fails_loudly():
    # Same "Microsoft does not say" gap as the out-of-range index above,
    # same choice: name the missing field rather than substitute "".
    with pytest.raises(EvalError, match=r"field 'missing' not found"):
        evaluate('Text.Format("#[missing]", [a = 1])')


def test_text_format_field_name_with_a_space():
    # M record field names may contain spaces (e.g. #"Company ID"); the
    # placeholder regex must not stop at the first space inside #[...].
    assert evaluate('Text.Format("#[Company Name]", [Company Name = "Acme"])') == "Acme"


# --------------------------------------------------------------------------
# Text.Format - shape mismatches and culture
# --------------------------------------------------------------------------


def test_text_format_list_token_against_a_record_argument_fails_loudly():
    with pytest.raises(EvalError, match="arguments is a record, not a list"):
        evaluate('Text.Format("#{0}", [a = 1])')


def test_text_format_record_token_against_a_list_argument_fails_loudly():
    with pytest.raises(EvalError, match="arguments is a list, not a record"):
        evaluate('Text.Format("#[a]", {1, 2})')


def test_text_format_arguments_must_be_a_list_or_a_record():
    with pytest.raises(EvalError, match="arguments must be a list.*or a record"):
        evaluate('Text.Format("hello", 42)')


def test_text_format_no_placeholders_passes_the_string_through():
    # Regression guard for the "arguments must be a list or a record" check
    # above firing even when the format string has nothing to substitute -
    # it must not, since Text.Format's own Syntax accepts `arguments as
    # any` and a caller with no placeholders should not be forced to shape
    # an unused argument as a list or record either... but this package
    # does still validate the shape eagerly (see the arguments-type test
    # above): this test exists to confirm a genuinely list/record argument
    # with zero matching tokens is a harmless no-op, not to relax that.
    assert evaluate('Text.Format("hello world", {})') == "hello world"


def test_text_format_rejects_non_invariant_culture():
    with pytest.raises(UnsupportedError, match="de-DE"):
        evaluate('Text.Format("#{0}", {1}, "de-DE")')


def test_text_format_null_argument_value_fails_loudly():
    # Text.From(null) is null, and embedding "null" as literal text would
    # be a silent, plausible-looking wrong answer (Rule 3) - this package
    # refuses instead of printing an empty string or the word "null".
    with pytest.raises(EvalError, match="null"):
        evaluate('Text.Format("#{0}", {null})')


# --------------------------------------------------------------------------
# Error.Record's own worked example 2 uses Text.Format twice inline - this
# is the example that originally surfaced "unknown identifier: Text.Format"
# via tests/test_doc_examples.py (not owned/edited by this task). Repeated
# here as a direct, fast-failing regression guard on this exact query
# shape, independent of the doc-examples corpus.
# --------------------------------------------------------------------------


def test_text_format_inside_error_record_example():
    query = """
        let
            CustomerId = 12345,
            result = try if CustomerId > 9999 then
                error Error.Record(
                    "CustomerNotFound",
                    Text.Format("Customer ID #{0} wasn't found.", {CustomerId}),
                    "Customer doesn't exist.",
                    {
                        Text.Format("Invalid ID = #{0}", {CustomerId}),
                        "Valid IDs: https://api.contoso.com/customers"
                    },
                    "ERR404"
                )
            else CustomerId
        in
            result
    """
    result = evaluate(query)
    assert result["Error"]["Message"] == "Customer ID 12345 wasn't found."
    assert result["Error"]["Message.Parameters"] == [
        "Invalid ID = 12345",
        "Valid IDs: https://api.contoso.com/customers",
    ]


# --------------------------------------------------------------------------
# Text.From - temporal and binary values (previously refused outright)
# --------------------------------------------------------------------------


def test_text_from_datetime_grounded_example():
    # Verbatim Example 2 from learn.microsoft.com/en-us/powerquery-m/text-from.
    assert (
        evaluate("Text.From(#datetime(2024, 6, 24, 14, 32, 22))")
        == "6/24/2024 2:32:22 PM"
    )


def test_text_from_date_grounded_via_text_format_example():
    # Text.From's own page has no bare #date example, but Text.Format's
    # Example 2 (tested above) renders #date(2015, 3, 10) as "3/10/2015"
    # while formatting a record field with Text.Format's own delegation to
    # Text.From - so this is the same value/culture pinned directly.
    assert evaluate("Text.From(#date(2015, 3, 10))") == "3/10/2015"


def test_text_from_duration_grounded_via_text_format_example():
    assert evaluate("Text.From(#duration(0, 0, 54, 40))") == "00:54:40"


def test_text_from_binary_grounded_example():
    # Verbatim Example 4 from Text.From's own page: base64, the same
    # default Binary.ToText already uses.
    assert evaluate('Text.From(Binary.FromText("10FF", BinaryEncoding.Hex))') == "EP8="


def test_text_from_agrees_with_date_totext_for_the_same_culture():
    # The contract the task brief states directly: Text.From on a temporal
    # must agree with this package's own *.ToText for the same value and
    # culture - checked here by calling both and comparing, rather than
    # only pinning one literal string.
    assert evaluate("Text.From(#date(2024,1,31))") == evaluate(
        'Date.ToText(#date(2024,1,31), "d")'
    )
    assert evaluate("Text.From(#datetime(2024,1,31,9,5,3))") == evaluate(
        'DateTime.ToText(#datetime(2024,1,31,9,5,3), "G")'
    )


def test_text_from_time_has_no_ms_grounding_but_agrees_with_time_totext():
    # DOCUMENTED CHOICE (no worked example exists anywhere in the M
    # reference for Text.From on a bare time value): this package agrees
    # with Time.ToText's own bare (no explicit format) default. Pinning the
    # literal string AND the agreement, so either a Text.From regression or
    # a Time.ToText default change is caught here.
    assert evaluate("Text.From(#time(14, 32, 22))") == "14:32:22"
    assert evaluate("Text.From(#time(14, 32, 22))") == evaluate(
        "Time.ToText(#time(14, 32, 22))"
    )


def test_text_from_datetimezone_has_no_ms_grounding_but_agrees_with_own_totext():
    # DOCUMENTED CHOICE, same reasoning as time above: agrees with
    # DateTimeZone.ToText's own bare default (an ISO string that keeps the
    # offset), not a guessed locale pattern.
    value = "#datetimezone(2024, 6, 24, 14, 32, 22, -7, 0)"
    assert evaluate(f"Text.From({value})") == evaluate(f"DateTimeZone.ToText({value})")
    assert evaluate(f"Text.From({value})") == "2024-06-24 14:32:22-07:00"


def test_text_from_rejects_non_invariant_culture_on_a_temporal_value():
    # Text.From's own Example 3 uses "de-DE" for exactly this reason - this
    # package implements only the invariant/en-US culture throughout.
    with pytest.raises(UnsupportedError, match="de-DE"):
        evaluate('Text.From(#datetime(2024, 6, 24, 14, 32, 22), "de-DE")')


def test_text_from_still_refuses_a_list_or_a_record():
    # Text.From's own About text lists number/date/time/datetime/
    # datetimezone/logical/duration/binary - a list or record is still not
    # one of those, and must still fail loudly rather than being silently
    # treated as one of the newly-added branches.
    with pytest.raises(EvalError, match="unsupported value type: list"):
        evaluate("Text.From({1, 2, 3})")


# --------------------------------------------------------------------------
# Number.From - temporal values (previously refused outright)
# --------------------------------------------------------------------------


def test_number_from_datetime_grounded_example():
    # Verbatim Example 2 from learn.microsoft.com/en-us/powerquery-m/number-from.
    assert evaluate("Number.From(#datetime(2020, 3, 20, 6, 0, 0))") == 43910.25


def test_number_from_date_is_a_whole_ole_serial():
    # About: "date: A double-precision floating-point number that contains
    # an OLE Automation date equivalent" - a date has no time-of-day, so
    # the fractional part must be exactly zero.
    assert evaluate("Number.From(#date(2020, 3, 20))") == 43910.0


def test_number_from_time_is_a_fractional_day():
    # About: "time: Expressed in fractional days." Noon is exactly
    # half a day.
    assert evaluate("Number.From(#time(12, 0, 0))") == 0.5
    assert evaluate("Number.From(#time(6, 0, 0))") == 0.25


def test_number_from_duration_agrees_with_duration_totaldays():
    # About: "duration: Expressed in whole and fractional days" - the same
    # arithmetic Duration.TotalDays already implements and this reuses.
    assert evaluate("Number.From(#duration(2, 5, 55, 20))") == evaluate(
        "Duration.TotalDays(#duration(2, 5, 55, 20))"
    )


def test_number_from_datetimezone_uses_the_local_wall_clock_not_utc():
    # About: "datetimezone: ... an OLE Automation date equivalent of the
    # LOCAL date and time of value" - the wall-clock fields as displayed in
    # the value's own offset, not shifted to UTC or the host's zone. A
    # regression that converted to UTC first would silently shift this
    # result by exactly the zone offset (7 hours here) instead of matching
    # the naive datetime with identical displayed fields.
    assert evaluate(
        "Number.From(#datetimezone(2024, 6, 24, 14, 32, 22, -7, 0))"
    ) == evaluate("Number.From(#datetime(2024, 6, 24, 14, 32, 22))")


def test_number_from_date_round_trips_through_date_from():
    # The task brief's own requirement: Number.From(Date.From(n)) == n
    # wherever the phantom-window doesn't forbid n outright. Reuses
    # _datetime.py's OLE epoch/window machinery on both sides (Date.From
    # going one way, Number.From's new branch going back), so this is a
    # true round-trip check, not two independently-typed constants.
    for n in (0, 1899, -5, 45467, 61, 40000):
        assert evaluate(f"Number.From(Date.From({n}))") == float(n)


def test_number_from_rejects_the_same_phantom_ole_window_date_from_does():
    # Serials 1-60 are the historical Excel/OLE 1900-phantom-leap-day
    # window that Date.From already refuses (_reject_phantom_ole_window in
    # _datetime.py, reused here rather than reimplemented) - a real
    # calendar date that lands in the equivalent window going forward
    # refuses the same way, by construction, not by a second hand-written
    # boundary that could silently drift from the first.
    with pytest.raises(UnsupportedError, match="phantom"):
        evaluate("Number.From(#date(1900, 1, 15))")


def test_number_from_still_refuses_a_list_or_a_record():
    with pytest.raises(EvalError, match="unsupported value type: list"):
        evaluate("Number.From({1, 2, 3})")
