"""Tests for the tail-of-the-catalog batch: ``Function.*``, ``Diagnostics.*``,
``Geography.*``/``Geometry.*``, ``Variable.*``, ``Embedded.Value``,
``Module.Versions``, ``Error.Record``, ``Comparer.Equals``.

Every assertion pinned to a Microsoft Learn worked example says so with the
exact example number, the same convention ``test_misc_builtins.py`` and
``test_comparers.py`` use. Several of these names cannot be exercised through
`evaluate()` the way Microsoft's own page writes them - a `type function
(...)` literal, in particular, cannot evaluate in this package at all (see
``_misc.py``'s Function.* module comment) - so those are pinned through
`bindings=` instead, which reaches the exact same runtime code path a working
`type function` value would.
"""

from __future__ import annotations

import pytest

from pqtools.builtins._type import _MType
from pqtools.evaluate import EvalError, UnsupportedError, evaluate

# --------------------------------------------------------------------------
# Function.From
# --------------------------------------------------------------------------


def test_function_from_docs_example_1_packs_args_into_a_list():
    # learn.microsoft.com/en-us/powerquery-m/function-from Example 1:
    # Function.From(type function (a as number, b as number) as number,
    # List.Sum)(2, 1) -> 3. The `type function (...)` literal itself cannot
    # evaluate in pqtools (see _misc.py's module comment) - `FnType` stands
    # in for it via --bind, which reaches Function.From's real code with a
    # placeholder for the one piece of the type it does not use.
    assert (
        evaluate("Function.From(FnType, List.Sum)(2, 1)", bindings={"FnType": None})
        == 3
    )


def test_function_from_docs_example_2_unary_function_over_the_list():
    # Example 2: a lambda receiving the packed list directly ->
    # list{0} & list{1} concatenates rather than adds.
    query = 'Function.From(FnType, (list) => list{0} & list{1})("2", "1")'
    assert evaluate(query, bindings={"FnType": None}) == "21"


def test_function_from_accepts_any_arity_from_the_call_site():
    # The returned function is a plain callable, not a `_Lambda` with a
    # fixed param count - it takes however many arguments a call site
    # gives it and packs exactly those into the list `function` receives.
    query = "Function.From(FnType, List.Sum)(1, 2, 3, 4)"
    assert evaluate(query, bindings={"FnType": None}) == 10


def test_a_type_function_literal_now_evaluates():
    """This pinned the upstream gap that made this batch's work unreachable.

    `type function (...) as T` was refused by the type-expression evaluator,
    one AST node ABOVE Function.From and Function.ScalarVector - so their own
    documented examples failed no matter how correctly those two were
    written. That gap was real and correctly reported; it is closed now, so
    the pin becomes an assertion that the shape works.
    """
    declared = evaluate("type function (a as number, b as number) as number")
    assert evaluate("Type.FunctionRequiredParameters(t)", bindings={"t": declared}) == 2
    assert evaluate("Type.FunctionReturn(t)", bindings={"t": declared}) == (
        evaluate("type number")
    )


# --------------------------------------------------------------------------
# Function.Invoke
# --------------------------------------------------------------------------


def test_function_invoke_docs_example():
    # learn.microsoft.com/en-us/powerquery-m/function-invoke's own example -
    # the only one of the four Function.* examples with no `type function
    # (...)` literal, so it runs unmodified end to end.
    query = "Function.Invoke(Record.FieldNames, {[A = 1, B = 2]})"
    assert evaluate(query) == ["A", "B"]


def test_function_invoke_rejects_a_non_list_argument_list():
    with pytest.raises(EvalError, match="list"):
        evaluate("Function.Invoke(Record.FieldNames, 1)")


def test_function_invoke_propagates_the_called_functions_own_error():
    with pytest.raises(EvalError):
        evaluate("Function.Invoke(List.Sum, {1, 2, 3})")  # wrong arity for List.Sum


# --------------------------------------------------------------------------
# Function.InvokeAfter
# --------------------------------------------------------------------------


def test_function_invoke_after_invokes_immediately_rather_than_sleeping():
    """Pins the deliberate choice for a function this page gives no
    worked example for.

    pqtools evaluates one expression tree synchronously with no scheduler
    to hand control back to, so a real `time.sleep(delay)` here would only
    make every query - and this suite - as slow as the longest delay any
    query happens to name, for zero difference in the RESULT. "Invoke now"
    is the honest behaviour for a deterministic, single-shot evaluator; a
    regression to an actual sleep would make this test (and the whole
    suite) measurably slower without changing what it asserts, which is
    exactly the signal that would catch it.
    """
    import time

    start = time.monotonic()
    result = evaluate("Function.InvokeAfter(() => 1 + 1, #duration(1, 0, 0, 0))")
    elapsed = time.monotonic() - start
    assert result == 2
    assert elapsed < 1.0


def test_function_invoke_after_rejects_a_non_duration_delay():
    # Catches the caller mistake this function's own arity check cannot:
    # passing a plain number instead of a #duration(...) value.
    with pytest.raises(EvalError, match="duration"):
        evaluate("Function.InvokeAfter(() => 1, 5)")


# --------------------------------------------------------------------------
# Function.ScalarVector
# --------------------------------------------------------------------------


# A stand-in for `type function (left as number, right as number) as
# number` / `type function (right as number, total as number) as number` -
# see _misc.py's module comment on why the real type literal cannot
# evaluate here. `_MType` already carries a `field_names` tuple for `type
# table [...]`; Function.ScalarVector duck-types onto that same attribute,
# so this is not a special-purpose test fixture, it is the shape the real
# type value would need to have too.
def _scalar_type(*names: str) -> _MType:
    return _MType(kind="function", display="type function (...)", field_names=names)


def test_function_scalarvector_docs_example_1():
    # learn.microsoft.com/en-us/powerquery-m/function-scalarvector Example
    # 1, run over --bind instead of the doc's own `type function (...)`
    # literal (see the module comment in _misc.py). Table.AddColumn calls
    # the scalar function once per row; each call builds its OWN one-row
    # table for vectorFunction, which is a degenerate (never-batched) case
    # of the documented contract - the doc's own Compute.ScoreVector
    # tolerates any chunk size down to one via Table.Split.
    query = """
    let
      Compute.ScoreScalar = (left, right) => left * right,
      Compute.ScoreVector = (input) => let
          chunks = Table.Split(input, 100),
          scoreChunk = (chunk) => Table.TransformRows(
              chunk, each Compute.ScoreScalar([left], [right])
          )
        in
          List.Combine(List.Transform(chunks, scoreChunk)),
      Compute.Score = Function.ScalarVector(ScalarType, Compute.ScoreVector),
      Final = Table.AddColumn(
        Table.FromRecords({[a = 1, b = 2], [a = 3, b = 4]}),
        "Result",
        each Compute.Score([a], [b])
      )
    in
      Final
    """
    result = evaluate(query, bindings={"ScalarType": _scalar_type("left", "right")})
    assert result == [
        {"a": 1, "b": 2, "Result": 2},
        {"a": 3, "b": 4, "Result": 12},
    ]


def test_function_scalarvector_docs_example_2_grade_computation():
    # Example 2's numeric core (the BatchId field is dropped by the doc's
    # own final Table.ExpandRecordColumn, so it is not part of this
    # assertion - a per-row BatchId would differ from the batched version
    # anyway, which is exactly the "performance differs, correctness does
    # not" property _misc.py's module comment describes). Table.AddColumn's
    # optional 4th argument (`type record` in the doc's own source) is
    # dropped too - unrelated to Function.ScalarVector, but `type record`
    # is a compound type builtins/_type.py does not model (only the
    # primitive M types are), a pre-existing gap this batch does not touch.
    query = """
    let
      _GradeTest = (right, total) => Number.Round(right / total, 2),
      _GradeTests = (inputs as table) as list => let
          batches = Table.Split(inputs, 2),
          gradeBatch = (batch as table) as list =>
            Table.TransformRows(batch, each [Grade = _GradeTest([right], [total])])
        in
          List.Combine(List.Transform(batches, gradeBatch)),
      GradeTest = Function.ScalarVector(ScalarType, _GradeTests),
      Tests = #table(
        type table [Test Name = text, Right = number, Total = number],
        {{"Quiz 1", 3, 4}, {"Test 1", 17, 22}, {"Quiz 2", 10, 10}}
      ),
      TestsWithGrades = Table.AddColumn(
        Tests, "Grade Info", each GradeTest([Right], [Total])
      ),
      Final = Table.ExpandRecordColumn(TestsWithGrades, "Grade Info", {"Grade"})
    in
      Final
    """
    result = evaluate(query, bindings={"ScalarType": _scalar_type("right", "total")})
    assert result == [
        {"Test Name": "Quiz 1", "Right": 3, "Total": 4, "Grade": 0.75},
        {"Test Name": "Test 1", "Right": 17, "Total": 22, "Grade": 0.77},
        {"Test Name": "Quiz 2", "Right": 10, "Total": 10, "Grade": 1},
    ]


def test_function_scalarvector_falls_back_to_positional_names_without_a_type():
    # A caller with no function-type value at all (the only kind reachable
    # through real M source today, since `type function (...)` cannot
    # evaluate) still gets a working scalar function - it just cannot name
    # its row's columns the way the real parameter names would.
    query = """
    let
      Score = Function.ScalarVector(
        null, each List.Transform(_, each [Column1] + [Column2])
      )
    in
      Score(2, 3)
    """
    assert evaluate(query) == 5


def test_function_scalarvector_rejects_a_vector_function_returning_the_wrong_length():
    query = "Function.ScalarVector(null, each {1, 2})(1, 2)"
    with pytest.raises(EvalError, match="one item per input row"):
        evaluate(query)


# --------------------------------------------------------------------------
# Diagnostics.Trace
# --------------------------------------------------------------------------


def test_diagnostics_trace_docs_example_delayed_value_is_invoked():
    # learn.microsoft.com/en-us/powerquery-m/diagnostics-trace's own (only)
    # example: Diagnostics.Trace(TraceLevel.Information,
    # "TextValueFromNumber", () => Text.From(123), true) -> "123". `value`
    # is a thunk BECAUSE delayed is true; Diagnostics.Trace calls it and
    # returns the call's result, not the thunk itself.
    query = (
        'Diagnostics.Trace(TraceLevel.Information, "TextValueFromNumber", '
        "() => Text.From(123), true)"
    )
    assert evaluate(query) == "123"


def test_diagnostics_trace_level_constants_match_tracelevel_type_page():
    # learn.microsoft.com/en-us/powerquery-m/tracelevel-type's "Allowed
    # values" table - a bit-flag scheme (1/2/4/8/16), not the 0..4 ordinal
    # a reader might guess without checking.
    assert evaluate("TraceLevel.Critical") == 1
    assert evaluate("TraceLevel.Error") == 2
    assert evaluate("TraceLevel.Warning") == 4
    assert evaluate("TraceLevel.Information") == 8
    assert evaluate("TraceLevel.Verbose") == 16


def test_diagnostics_trace_without_delayed_returns_value_unchanged():
    # delayed omitted (defaults to not-delayed): `value` is a plain value,
    # not a thunk, and passes straight through - this is the half of the
    # function's contract with an observable result; the tracing half has
    # nowhere to go in a library with no host diagnostics pane (see
    # _misc.py's comment) and is a deliberate no-op, not a bug.
    assert evaluate('Diagnostics.Trace(TraceLevel.Warning, "msg", 42)') == 42
    assert evaluate('Diagnostics.Trace(TraceLevel.Warning, "msg", 42, false)') == 42


def test_diagnostics_trace_rejects_a_null_message():
    # `message as anynonnull` - the one type constraint the Syntax block
    # states beyond "any".
    with pytest.raises(EvalError, match="message"):
        evaluate("Diagnostics.Trace(TraceLevel.Warning, null, 1)")


def test_diagnostics_trace_delayed_requires_a_function_value():
    with pytest.raises(EvalError, match="function"):
        evaluate('Diagnostics.Trace(TraceLevel.Warning, "msg", 1, true)')


# --------------------------------------------------------------------------
# Error.Record
# --------------------------------------------------------------------------


def test_error_record_docs_example_1_bare_call():
    # learn.microsoft.com/en-us/powerquery-m/error-record Example 1's own
    # `Error = [...]` sub-record, called bare (not wrapped in
    # `error ... `/`try`) so the full six-field shape is observable. See
    # test_error_record_try_wrapped_loses_three_fields below for why the
    # wrapped form cannot show all six here.
    result = evaluate(
        'Error.Record("DivideByZero", "You attempted to divide by zero.")'
    )
    assert result == {
        "Reason": "DivideByZero",
        "Message": "You attempted to divide by zero.",
        "Detail": None,
        "Message.Format": None,
        "Message.Parameters": None,
        "ErrorCode": None,
    }


def test_error_record_docs_example_2_bare_call():
    # Example 2: message and parameters BOTH given - Message.Format
    # mirrors Message exactly (see the field-order comment in _misc.py for
    # why that is pinned to the examples, not to a stated rule).
    query = (
        'Error.Record("CustomerNotFound", '
        '"Customer ID 12345 wasn\'t found.", '
        '"Customer doesn\'t exist.", '
        '{"Invalid ID = 12345", "Valid IDs: https://api.contoso.com/customers"}, '
        '"ERR404")'
    )
    assert evaluate(query) == {
        "Reason": "CustomerNotFound",
        "Message": "Customer ID 12345 wasn't found.",
        "Detail": "Customer doesn't exist.",
        "Message.Format": "Customer ID 12345 wasn't found.",
        "Message.Parameters": [
            "Invalid ID = 12345",
            "Valid IDs: https://api.contoso.com/customers",
        ],
        "ErrorCode": "ERR404",
    }


def test_error_record_message_format_is_null_without_parameters():
    # The rule Example 1 and Example 2 pin between them: Message.Format
    # tracks `message` only when `parameters` is ALSO given.
    with_params = evaluate('Error.Record("R", "M", null, {"p"})')
    without_params = evaluate('Error.Record("R", "M")')
    assert with_params["Message.Format"] == "M"
    assert without_params["Message.Format"] is None


def test_error_record_survives_try_with_every_documented_field():
    """The divergence this documented has been fixed, exactly as predicted.

    It used to assert three fields, because `evaluate._eval_error_raising`
    reprojected whatever `error <record>` raised onto Reason/Message/Detail
    and silently DELETED the rest - so Microsoft's own worked Example 1 came
    back missing half of its documented output. That function now carries
    the author's record through unchanged, and this test asserts the full
    six-field shape it asked its successor to assert.
    """
    query = """let
    input = 100,
    divisor = 0,
    result = try if divisor = 0 then
        error Error.Record("DivideByZero", "You attempted to divide by zero.")
    else
        input / divisor
    in
    result"""
    assert evaluate(query) == {
        "HasError": True,
        "Error": {
            "Reason": "DivideByZero",
            "Message": "You attempted to divide by zero.",
            "Detail": None,
            "Message.Format": None,
            "Message.Parameters": None,
            "ErrorCode": None,
        },
    }


def test_error_record_requires_at_least_a_reason():
    with pytest.raises(UnsupportedError, match="Error.Record"):
        evaluate("Error.Record()")


# --------------------------------------------------------------------------
# Comparer.Equals
# --------------------------------------------------------------------------


def test_comparer_equals_with_ordinal_comparer():
    # The page's only worked example (Comparer.FromCulture("en-US")) never
    # reaches this function - Comparer.FromCulture raises on that call
    # before Comparer.Equals is invoked - so this is pinned against the
    # two comparers pqtools implements instead (Comparer.Ordinal(x, y) = 0
    # is independently verified in test_comparers.py).
    assert evaluate('Comparer.Equals(Comparer.Ordinal, "a", "a")') is True
    assert evaluate('Comparer.Equals(Comparer.Ordinal, "a", "b")') is False


def test_comparer_equals_with_ordinal_ignore_case():
    assert evaluate('Comparer.Equals(Comparer.OrdinalIgnoreCase, "ABC", "abc")') is True


def test_comparer_equals_from_culture_still_refuses_when_called():
    # Unchanged behaviour: Comparer.FromCulture("en-US") raises before
    # Comparer.Equals is ever invoked - this is what makes the page's own
    # example unreachable here, pinned so a future change is deliberate.
    with pytest.raises(UnsupportedError, match="Comparer.FromCulture"):
        evaluate('Comparer.Equals(Comparer.FromCulture("en-US"), "1", "A")')


def test_comparer_equals_rejects_a_non_function_comparer():
    with pytest.raises(EvalError, match="not a function"):
        evaluate('Comparer.Equals(5, "a", "a")')


def test_comparer_equals_rejects_a_comparer_that_returns_a_non_number():
    with pytest.raises(EvalError, match="number"):
        evaluate('Comparer.Equals((x, y) => "nope", 1, 1)')


# --------------------------------------------------------------------------
# GeographyPoint.From / Geography.FromWellKnownText / Geography.ToWellKnownText
#
# Neither of these three pages, nor Geometry's mirror pair, carries a
# single worked example - so no OUTPUT record shape is pinned by Microsoft
# anywhere. The record shape asserted below (Kind = "Point" plus
# Longitude/Latitude/Z/M/SRID, or X/Y/Z/M/SRID for Geometry) is a
# pqtools-internal convention chosen for internal consistency across all
# six functions - see the module comment in _misc.py for the full
# reasoning, including the one grounded clue the Syntax block itself gives
# (the existence of `optional omitSRID` implies the default WKT text
# already carries a SRID, which is why it uses the EWKT `SRID=<n>;`
# prefix by default rather than bare OGC WKT).
# --------------------------------------------------------------------------


def test_geographypoint_from_default_srid_is_4326():
    # "An optional spatial reference identifier (SRID) can be given if
    # different from the default value (4326)" - the page's own About
    # text.
    assert evaluate("GeographyPoint.From(1, 2)") == {
        "Kind": "Point",
        "Longitude": 1,
        "Latitude": 2,
        "Z": None,
        "M": None,
        "SRID": 4326,
    }


def test_geographypoint_from_with_z_m_and_explicit_srid():
    assert evaluate("GeographyPoint.From(1, 2, 3, 4, 9999)") == {
        "Kind": "Point",
        "Longitude": 1,
        "Latitude": 2,
        "Z": 3,
        "M": 4,
        "SRID": 9999,
    }


def test_geometrypoint_from_default_srid_is_0():
    # Geometry's own page states its default SRID is 0, not Geography's
    # 4326 - checked on this page specifically, not assumed shared.
    assert evaluate("GeometryPoint.From(1, 2)") == {
        "Kind": "Point",
        "X": 1,
        "Y": 2,
        "Z": None,
        "M": None,
        "SRID": 0,
    }


def test_geography_to_well_known_text_default_includes_srid_prefix():
    assert (
        evaluate("Geography.ToWellKnownText(GeographyPoint.From(1, 2))")
        == "SRID=4326;POINT(1 2)"
    )


def test_geography_to_well_known_text_omit_srid():
    query = "Geography.ToWellKnownText(GeographyPoint.From(1, 2), true)"
    assert evaluate(query) == "POINT(1 2)"


def test_geography_to_well_known_text_with_z_and_m():
    query = "Geography.ToWellKnownText(GeographyPoint.From(1, 2, 3, 4), true)"
    assert evaluate(query) == "POINT ZM(1 2 3 4)"


def test_geometry_to_well_known_text_uses_x_y_and_default_srid_zero():
    assert (
        evaluate("Geometry.ToWellKnownText(GeometryPoint.From(1, 2))")
        == "SRID=0;POINT(1 2)"
    )


def test_geography_from_well_known_text_round_trips_geographypoint_from():
    # Geography.FromWellKnownText(Geography.ToWellKnownText(p)) reproduces
    # `p` for every point built by GeographyPoint.From - the internal
    # consistency this batch's design note commits to, in place of a
    # worked example Microsoft does not provide.
    query = """
    let
        Point = GeographyPoint.From(12.5, -8.25, 3, 4, 4326),
        Text = Geography.ToWellKnownText(Point),
        Back = Geography.FromWellKnownText(Text)
    in
        Back
    """
    assert evaluate(query) == {
        "Kind": "Point",
        "Longitude": 12.5,
        "Latitude": -8.25,
        "Z": 3,
        "M": 4,
        "SRID": 4326,
    }


def test_geometry_from_well_known_text_parses_z_dimension():
    result = evaluate('Geometry.FromWellKnownText("POINT Z (1 2 3)")')
    assert result == {"Kind": "Point", "X": 1, "Y": 2, "Z": 3, "M": None, "SRID": 0}


def test_geography_from_well_known_text_null_propagates():
    assert evaluate("Geography.FromWellKnownText(null)") is None


def test_geography_to_well_known_text_null_propagates():
    assert evaluate("Geography.ToWellKnownText(null)") is None


def test_geometry_from_well_known_text_refuses_non_point_geometry_by_name():
    # LINESTRING/POLYGON/MULTIPOINT/... have no documented record shape
    # anywhere pqtools was told to ground against - refused rather than
    # invented, matching catalog.py's own "shape" reasoning for Xml.Document
    # et al.
    with pytest.raises(UnsupportedError, match="only POINT well-known text"):
        evaluate('Geometry.FromWellKnownText("LINESTRING(0 0, 1 1)")')


def test_geography_from_well_known_text_refuses_point_empty():
    with pytest.raises(UnsupportedError, match="POINT EMPTY"):
        evaluate('Geography.FromWellKnownText("POINT EMPTY")')


def test_geography_from_well_known_text_refuses_wrong_coordinate_count():
    with pytest.raises(EvalError, match="coordinate"):
        evaluate('Geography.FromWellKnownText("POINT(1 2 3)")')


def test_geography_to_well_known_text_refuses_a_non_point_shaped_record():
    with pytest.raises(UnsupportedError, match="Point-shaped record"):
        evaluate('Geography.ToWellKnownText([Kind = "LineString"])')


def test_geography_to_well_known_text_refuses_a_record_missing_coordinates():
    with pytest.raises(EvalError, match="Longitude"):
        evaluate('Geography.ToWellKnownText([Kind = "Point", Latitude = 2])')


# --------------------------------------------------------------------------
# Reclassified as "engine"/"internal" in scripts/sync_m_catalog.py - real
# Power Query functions this evaluator cannot honestly answer, because the
# question is about the Mashup Engine's own session/host state
# (Diagnostics.ActivityId/CorrelationId, Variable.Value/ValueOrDefault,
# Function.IsDataSource, Module.Versions) or is explicitly marked internal
# use only in Microsoft's own reference (Embedded.Value). Each assertion
# below pins that the message now names the REAL reason rather than
# catalog.py's generic "not implemented yet" (a query using one is not
# waiting on a pqtools release; it needs a host pqtools cannot be).
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "Diagnostics.ActivityId()",
        "Diagnostics.CorrelationId()",
        "Function.IsDataSource(each _)",
        "Module.Versions()",
        'Variable.Value("x")',
        'Variable.ValueOrDefault("x")',
    ],
)
def test_engine_scoped_names_explain_the_mashup_engine_gap(call: str) -> None:
    with pytest.raises(UnsupportedError, match="Mashup Engine"):
        evaluate(call)


def test_embedded_value_explains_itself_as_internal_use_only():
    # "This function is intended for internal use only" - Microsoft's own
    # About text for this page, word for word what the "internal" reason
    # in catalog.py's REASONS dict names.
    with pytest.raises(UnsupportedError, match="internal use only"):
        evaluate('Embedded.Value(1, "a")')


def test_reclassified_names_no_longer_say_not_implemented_yet():
    # The bug this whole batch's Job 2 exists to fix: these are real,
    # permanently-refused functions, not ones waiting on a pqtools
    # release - the old "notyet" message pointed the reader at a GitHub
    # issue that would never close it.
    for call in (
        "Diagnostics.ActivityId()",
        "Diagnostics.CorrelationId()",
        "Module.Versions()",
        'Variable.Value("x")',
        'Variable.ValueOrDefault("x")',
        'Embedded.Value(1, "a")',
        "Function.IsDataSource(each _)",
    ):
        with pytest.raises(UnsupportedError) as excinfo:
            evaluate(call)
        assert "not implemented yet" not in str(excinfo.value)
