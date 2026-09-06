"""pqtools.export: to_pandas/to_arrow/to_parquet and the pqtools.open()
facade.

pandas and pyarrow are optional - every test that touches either library
starts with `pytest.importorskip`, so this file collects and (mostly) runs
in an environment with neither installed. The one exception is the
missing-extras tests themselves, which use `monkeypatch.setitem(sys.modules,
"pandas", None)` to force the ImportError branch deterministically whether
or not the real package happens to be installed in the environment running
the suite - see test_to_pandas_missing_extra_names_install_command.
"""

from __future__ import annotations

import datetime
import math
import subprocess
import sys
from pathlib import Path

import pytest

import pqtools
from pqtools.export import ExportRefusal, to_arrow, to_pandas, to_parquet

# --------------------------------------------------------------------------
# Missing extras: the base environment must never break
# --------------------------------------------------------------------------


def test_plain_import_needs_neither_extra():
    # If this module's imports weren't lazy, collecting this file at all
    # would already have failed in a pandas/pyarrow-less environment.
    assert pqtools.to_pandas is to_pandas
    assert pqtools.to_arrow is to_arrow
    assert pqtools.to_parquet is to_parquet
    assert pqtools.ExportRefusal is ExportRefusal
    assert pqtools.open is pqtools.export.open


def test_to_pandas_missing_extra_names_install_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "pandas", None)
    with pytest.raises(ExportRefusal, match=r"pip install 'pqtools\[pandas\]'"):
        to_pandas([])


def test_to_arrow_missing_extra_names_install_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(ExportRefusal, match=r"pip install 'pqtools\[arrow\]'"):
        to_arrow([])


def test_to_parquet_missing_extra_names_install_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(ExportRefusal, match=r"pip install 'pqtools\[arrow\]'"):
        to_parquet([], "unused.parquet")


def test_export_module_survives_missing_extras_in_a_fresh_interpreter():
    """Proves the lazy-import discipline, not just its effect, in a process
    that never shares state with this test session.

    An in-process `importlib.reload` under a poisoned `sys.modules` was
    tried here first and rejected: reload rebinds `pqtools.export`'s names
    to new objects in place, but every other test in this file (and
    `pqtools.export`'s own already-imported functions still referenced
    elsewhere) keeps its pre-reload references - so a later test's
    `pytest.raises(ExportRefusal, ...)` can hold a *different* class object
    than the one actually raised, and fail for a reason that has nothing to
    do with the code under test. A subprocess has no such shared state: if
    `export.py` ever grew a module-scope `import pandas`/`import pyarrow`,
    `import pqtools` itself would fail here, before either function is even
    called.
    """
    script = (
        "import sys\n"
        "sys.modules['pandas'] = None\n"
        "sys.modules['pyarrow'] = None\n"
        "import pqtools\n"
        "try:\n"
        "    pqtools.to_pandas([])\n"
        "except pqtools.ExportRefusal as error:\n"
        '    assert "pqtools[pandas]" in str(error), error\n'
        "else:\n"
        "    raise SystemExit('to_pandas did not raise ExportRefusal')\n"
        "try:\n"
        "    pqtools.to_arrow([])\n"
        "except pqtools.ExportRefusal as error:\n"
        '    assert "pqtools[arrow]" in str(error), error\n'
        "else:\n"
        "    raise SystemExit('to_arrow did not raise ExportRefusal')\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "OK"


# --------------------------------------------------------------------------
# The type map - one test per row
# --------------------------------------------------------------------------


def test_type_map_logical():
    pd = pytest.importorskip("pandas")
    rows = [{"b": True}, {"b": False}, {"b": None}]
    df = to_pandas(rows)
    assert str(df["b"].dtype) == "boolean"
    assert df["b"].tolist() == [True, False, pd.NA]

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("b").type == pa.bool_()
    assert table.column("b").to_pylist() == [True, False, None]


def test_type_map_number_pure_int_uses_nullable_int64_not_float():
    """The named risk: an M number column with a null must not become
    float64 and silently turn an exact int into `1.0`/NaN."""
    pytest.importorskip("pandas")
    rows = [{"n": 1}, {"n": 2}, {"n": None}]
    df = to_pandas(rows)
    assert str(df["n"].dtype) == "Int64"
    # `to_dict` is the round-trip a caller actually uses; it turns the
    # column's own `<NA>` marker back into plain `None`, and the values
    # come back as exact integers (1, 2) - never promoted to float (1.0,
    # 2.0) the way a plain `float64` column would have forced them to be.
    round_tripped = df["n"].to_dict()
    assert round_tripped == {0: 1, 1: 2, 2: None}
    assert all(type(v) is int for v in round_tripped.values() if v is not None)

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("n").type == pa.int64()
    assert table.column("n").to_pylist() == [1, 2, None]


def test_type_map_number_with_a_float_uses_float64():
    pytest.importorskip("pandas")
    rows = [{"n": 1}, {"n": 2.5}, {"n": None}]
    df = to_pandas(rows)
    assert str(df["n"].dtype) == "float64"
    assert df["n"].tolist()[0] == 1.0
    assert df["n"].tolist()[1] == 2.5
    assert math.isnan(df["n"].tolist()[2])

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("n").type == pa.float64()
    assert table.column("n").to_pylist() == [1.0, 2.5, None]


def test_type_map_text_preserves_null_as_none_not_nan():
    """pandas 3.0's default string dtype represents a missing cell as float
    `nan`, conflating a null with a different sentinel - verified 2026-09-06.
    export.py forces `object` dtype specifically to avoid that."""
    pytest.importorskip("pandas")
    rows = [{"t": "hello"}, {"t": None}]
    df = to_pandas(rows)
    assert str(df["t"].dtype) == "object"
    assert df["t"].tolist() == ["hello", None]

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("t").type == pa.string()
    assert table.column("t").to_pylist() == ["hello", None]


def test_type_map_binary():
    pytest.importorskip("pandas")
    rows = [{"bin": b"\x01\x02"}, {"bin": None}]
    df = to_pandas(rows)
    assert str(df["bin"].dtype) == "object"
    assert df["bin"].tolist() == [b"\x01\x02", None]

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("bin").type == pa.binary()
    assert table.column("bin").to_pylist() == [b"\x01\x02", None]


def test_type_map_date():
    pytest.importorskip("pandas")
    rows = [{"d": datetime.date(2024, 1, 1)}, {"d": None}]
    df = to_pandas(rows)
    assert str(df["d"].dtype) == "object"
    assert df["d"].tolist() == [datetime.date(2024, 1, 1), None]

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("d").type == pa.date32()
    assert table.column("d").to_pylist() == [datetime.date(2024, 1, 1), None]


def test_type_map_time():
    pytest.importorskip("pandas")
    rows = [{"t": datetime.time(10, 30, 15)}, {"t": None}]
    df = to_pandas(rows)
    assert str(df["t"].dtype) == "object"
    assert df["t"].tolist() == [datetime.time(10, 30, 15), None]

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("t").type == pa.time64("us")
    assert table.column("t").to_pylist() == [datetime.time(10, 30, 15), None]


def test_type_map_duration():
    pytest.importorskip("pandas")
    rows = [{"dur": datetime.timedelta(days=1, hours=2)}, {"dur": None}]
    df = to_pandas(rows)
    assert str(df["dur"].dtype) == "timedelta64[us]"
    assert df["dur"].tolist()[0] == datetime.timedelta(days=1, hours=2)
    assert df["dur"].isna().tolist() == [False, True]

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("dur").type == pa.duration("us")
    assert table.column("dur").to_pylist() == [
        datetime.timedelta(days=1, hours=2),
        None,
    ]


def test_type_map_datetime_naive():
    pytest.importorskip("pandas")
    rows = [{"dt": datetime.datetime(2024, 1, 1, 10, 30)}, {"dt": None}]
    df = to_pandas(rows)
    assert str(df["dt"].dtype) == "datetime64[us]"
    assert df["dt"].tolist()[0] == datetime.datetime(2024, 1, 1, 10, 30)
    assert df["dt"].isna().tolist() == [False, True]

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("dt").type == pa.timestamp("us")
    assert table.column("dt").to_pylist() == [
        datetime.datetime(2024, 1, 1, 10, 30),
        None,
    ]


def test_type_map_datetimezone_same_offset():
    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    pytest.importorskip("pandas")
    rows = [
        {"dtz": datetime.datetime(2024, 1, 1, 10, 30, tzinfo=tz)},
        {"dtz": None},
    ]
    df = to_pandas(rows)
    assert str(df["dtz"].dtype) == "datetime64[us, UTC+05:30]"
    assert df["dtz"].tolist()[0] == datetime.datetime(2024, 1, 1, 10, 30, tzinfo=tz)

    pa = pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.schema.field("dtz").type == pa.timestamp("us", tz="+05:30")
    assert table.column("dtz").to_pylist()[0] == datetime.datetime(
        2024, 1, 1, 10, 30, tzinfo=tz
    )


def test_type_map_null_only_column():
    pytest.importorskip("pandas")
    rows = [{"n": None}, {"n": None}]
    df = to_pandas(rows)
    assert str(df["n"].dtype) == "object"
    assert df["n"].tolist() == [None, None]

    pytest.importorskip("pyarrow")
    table = to_arrow(rows)
    assert table.column("n").to_pylist() == [None, None]


def test_empty_row_list_is_a_frame_with_no_columns():
    pd = pytest.importorskip("pandas")
    df = to_pandas([])
    assert isinstance(df, pd.DataFrame)
    assert df.shape == (0, 0)

    pytest.importorskip("pyarrow")
    table = to_arrow([])
    assert table.num_rows == 0
    assert table.num_columns == 0


# --------------------------------------------------------------------------
# Refusals - one per unmappable case
# --------------------------------------------------------------------------


def test_refuses_ragged_rows_rather_than_filling_a_null():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    with pytest.raises(ExportRefusal, match="ragged rows are refused"):
        to_pandas([{"a": 1, "b": 2}, {"a": 3}])


def test_refuses_a_column_that_mixes_m_types():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    with pytest.raises(ExportRefusal, match="mixes M types"):
        to_pandas([{"a": 1}, {"a": "text"}])


def test_refuses_an_unread_deferred_table():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    table = pqtools.DeferredTable(lambda: [], 'Source{[Item="Orders"]}[Data]')
    with pytest.raises(ExportRefusal, match="column 'data'.*not been read"):
        to_pandas([{"data": table}])


def test_refuses_a_nested_record_cell():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    with pytest.raises(ExportRefusal, match="column 'rec'.*an M record"):
        to_pandas([{"rec": {"x": 1}}])


def test_refuses_a_nested_list_cell():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    with pytest.raises(ExportRefusal, match="column 'lst'.*nested table"):
        to_pandas([{"lst": [1, 2, 3]}])


def test_refuses_a_type_value_cell():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    from pqtools.evaluate import evaluate

    type_value = evaluate("type text")
    with pytest.raises(ExportRefusal, match="column 'ty'.*an M type value"):
        to_pandas([{"ty": type_value}])


def test_refuses_a_function_value_cell():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    from pqtools.evaluate import evaluate

    function_value = evaluate("() => 1")
    with pytest.raises(ExportRefusal, match="column 'f'.*an M function value"):
        to_pandas([{"f": function_value}])


def test_refuses_mixed_utc_offsets_in_one_datetimezone_column():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    tz1 = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    tz2 = datetime.UTC
    rows = [
        {"dtz": datetime.datetime(2024, 1, 1, 10, 30, tzinfo=tz1)},
        {"dtz": datetime.datetime(2024, 1, 1, 10, 30, tzinfo=tz2)},
    ]
    with pytest.raises(ExportRefusal, match="different UTC offsets"):
        to_pandas(rows)


def test_refuses_null_next_to_nan_in_a_number_column():
    """Both collapse to pandas float64 NaN, so exporting them together would
    make a real M #nan indistinguishable from a null after the fact."""
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    rows = [{"n": 1.5}, {"n": None}, {"n": float("nan")}]
    with pytest.raises(ExportRefusal, match="both null and the M number #nan"):
        to_pandas(rows)


def test_refuses_an_integer_beyond_64_bit_range():
    # Guarded even though the refusal is pandas-independent logic:
    # `to_pandas` reports a missing pandas BEFORE it validates rows, which
    # is the right order for a caller who cannot act on a data complaint
    # until the library is installed. Without this the test asserts the
    # data message and gets the install message in a bare environment.
    pytest.importorskip("pandas")
    rows = [{"n": 2**70}]
    with pytest.raises(ExportRefusal, match="64-bit integer range"):
        to_pandas(rows)
    with pytest.raises(ExportRefusal, match="64-bit integer range"):
        to_arrow(rows)


# --------------------------------------------------------------------------
# Round trip on a real, multi-type M query
# --------------------------------------------------------------------------

_ROUND_TRIP_QUERY = """
#table(
    {"b", "i", "f", "t", "bin", "d", "tm", "dt", "dtz", "dur"},
    {
        {true, 1, 1.5, "hello", #binary({1, 2, 3}), #date(2024, 1, 1),
         #time(10, 30, 15), #datetime(2024, 1, 1, 10, 30, 0),
         #datetimezone(2024, 1, 1, 10, 30, 0, 5, 30), #duration(1, 2, 3, 4)},
        {false, 2, 2.5, "world", #binary({4, 5, 6}), #date(2024, 1, 2),
         #time(11, 0, 0), #datetime(2024, 1, 2, 0, 0, 0),
         #datetimezone(2024, 1, 2, 0, 0, 0, 5, 30), #duration(0, 1, 0, 0)}
    }
)
"""


def test_round_trip_pandas_preserves_every_mapped_type():
    pd = pytest.importorskip("pandas")
    rows = pqtools.evaluate(_ROUND_TRIP_QUERY)
    df = to_pandas(rows)
    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

    assert df["b"].tolist() == [True, False]
    assert df["i"].tolist() == [1, 2]
    assert df["f"].tolist() == [1.5, 2.5]
    assert df["t"].tolist() == ["hello", "world"]
    assert df["bin"].tolist() == [b"\x01\x02\x03", b"\x04\x05\x06"]
    assert df["d"].tolist() == [datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)]
    assert df["tm"].tolist() == [
        datetime.time(10, 30, 15),
        datetime.time(11, 0, 0),
    ]
    assert df["dt"].tolist() == [
        pd.Timestamp(2024, 1, 1, 10, 30, 0),
        pd.Timestamp(2024, 1, 2, 0, 0, 0),
    ]
    assert df["dtz"].tolist() == [
        datetime.datetime(2024, 1, 1, 10, 30, tzinfo=tz),
        datetime.datetime(2024, 1, 2, 0, 0, tzinfo=tz),
    ]
    assert df["dur"].tolist() == [
        datetime.timedelta(days=1, hours=2, minutes=3, seconds=4),
        datetime.timedelta(hours=1),
    ]


def test_round_trip_arrow_and_parquet_preserve_every_mapped_type(tmp_path):
    pytest.importorskip("pandas")
    pq = pytest.importorskip("pyarrow.parquet")
    rows = pqtools.evaluate(_ROUND_TRIP_QUERY)
    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

    table = to_arrow(rows)
    assert table.to_pylist() == rows

    path = tmp_path / "roundtrip.parquet"
    to_parquet(rows, path)
    read_back = pq.read_table(path).to_pylist()
    assert read_back == rows
    assert read_back[0]["dtz"].tzinfo.utcoffset(None) == tz.utcoffset(None)


# --------------------------------------------------------------------------
# pqtools.open() - discoverability, no evaluation until .eval()
# --------------------------------------------------------------------------


def test_open_reads_a_section_document_with_several_members(tmp_path: Path):
    path = tmp_path / "queries.pq"
    path.write_text(
        'section Section1; shared Sales = 1 + 1; shared Name = "hi";',
        encoding="utf-8",
    )
    handle = pqtools.open(path)
    assert sorted(handle.queries) == ["Name", "Sales"]
    # The EXPRESSION, not the `shared NAME = ...;` statement - the same text
    # `pq show --member Sales` prints. The two surfaces disagreed until the
    # round-11 review pointed out that a reader has no way to know which one
    # "a query's source" means; see test_end_to_end.py for the test that
    # holds them together.
    assert handle.source("Sales") == "1 + 1"
    assert handle.eval("Sales") == 2
    assert handle.eval("Name") == "hi"


def test_open_reads_a_bare_expression_file_by_its_stem(tmp_path: Path):
    path = tmp_path / "Orders.pq"
    path.write_text("let x = 1, y = 2 in x + y", encoding="utf-8")
    handle = pqtools.open(path)
    assert handle.queries == ["Orders"]
    assert handle.source("Orders") == "let x = 1, y = 2 in x + y"
    assert handle.eval("Orders") == 3


def test_open_source_never_evaluates(tmp_path: Path, monkeypatch):
    """`.source()` must not call `evaluate()` at all - a query with a
    deliberately unsupported construct should still be readable as text."""
    path = tmp_path / "Broken.pq"
    path.write_text("Nonexistent.Function(1)", encoding="utf-8")
    handle = pqtools.open(path)
    assert handle.source("Broken") == "Nonexistent.Function(1)"
    with pytest.raises(pqtools.UnsupportedError):
        handle.eval("Broken")


def test_open_unknown_query_name_is_a_typed_error(tmp_path: Path):
    path = tmp_path / "Orders.pq"
    path.write_text("1 + 1", encoding="utf-8")
    handle = pqtools.open(path)
    with pytest.raises(pqtools.MQueryError, match="no query named 'Missing'"):
        handle.source("Missing")
    with pytest.raises(pqtools.MQueryError, match="no query named 'Missing'"):
        handle.eval("Missing")


def test_open_has_no_transform_verbs():
    """PRD-pandas-for-powerquery-2026-09-06.md s3: a thin facade, not a
    DataFrame clone - .filter()/.groupby() must not exist."""
    assert not hasattr(pqtools.export.PqFile, "filter")
    assert not hasattr(pqtools.export.PqFile, "groupby")
