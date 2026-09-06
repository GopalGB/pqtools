"""Optional bulk-data export: table rows out of ``evaluate()`` into pandas,
Arrow, or a parquet file - plus ``open()``, a read-only handle for
discovering and reading the queries in a file without evaluating them.

pandas and pyarrow are OPTIONAL, exactly like the connector extras in
``pyproject.toml``. Every import of either library happens inside the
function that needs it, never at module scope, so ``import pqtools`` and
``import pqtools.export`` both succeed with neither installed - the
CLI/linter half of this package must never drag in a data-science stack. A
caller who reaches for one of these functions without the extra installed
gets a typed :class:`ExportRefusal` naming the exact ``pip install``
command, not a bare ``ImportError``.

The type map below is the point of this module (see
``.planning/PRD-pandas-for-powerquery-2026-09-06.md`` s4). The Python types
it dispatches on are exactly the ones this evaluator's data model already
uses - the same ones ``builtins/_shared.py::_type_name`` and
``builtins/_type.py::_classify`` check, in the same order (``datetime``
before ``date`` because ``datetime.datetime`` subclasses ``datetime.date``;
``bool`` before ``int`` because ``bool`` subclasses ``int``). Anything
neither of those two calls a scalar M type - a record, a list/table cell,
a ``type`` value, a ``function`` value, or an unread :class:`DeferredTable`
- refuses by name rather than being coerced into ``object``/``NaN``.
"""

from __future__ import annotations

import datetime
import math
from pathlib import Path
from typing import Any, NoReturn

from . import containers
from .builtins._shared import DeferredTable
from .builtins._type import _MType
from .containers import read_sections, split_shared
from .core import MQueryError, _snapshot
from .evaluate import evaluate
from .io import DENY_ALL, IOPolicy


class ExportRefusal(MQueryError):
    code = "M_EXPORT_REFUSED"


def _install_hint(extra: str) -> str:
    return f"pip install 'pqtools[{extra}]'"


def _require_pandas() -> Any:
    try:
        import pandas as pd  # type: ignore[import-untyped]
    except ImportError as error:
        raise ExportRefusal(
            f"pandas is not installed. Install it with: {_install_hint('pandas')}"
        ) from error
    return pd


def _require_pyarrow() -> Any:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
    except ImportError as error:
        raise ExportRefusal(
            f"pyarrow is not installed. Install it with: {_install_hint('arrow')}"
        ) from error
    return pa


# --------------------------------------------------------------------------
# The type map
# --------------------------------------------------------------------------


# "number-int"/"number-float" are this module's own split of M's single
# `number` kind, not a distinction the evaluator itself makes (_type_name
# calls both "number"). It exists because pandas has no dtype that holds a
# *nullable* 64-bit integer without silently promoting the whole column to
# float64 - turning an exact `2` into `2.0` and, past 2**53, actually losing
# precision. That is the "null inside an integer column" risk named in
# PRD-pandas-for-powerquery-2026-09-06.md s4, so it is tracked here and only
# reunited into one `float64` column the moment a real float shows up next
# to it (see `_pandas_number_column`/`_arrow_number_array`).
def _cell_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, DeferredTable):
        return "table"
    if isinstance(value, bool):
        return "logical"
    if isinstance(value, int):
        return "number-int"
    if isinstance(value, float):
        return "number-float"
    if isinstance(value, str):
        return "text"
    if isinstance(value, bytes):
        return "binary"
    if isinstance(value, datetime.datetime):
        return "datetimezone" if value.tzinfo is not None else "datetime"
    if isinstance(value, datetime.date):
        return "date"
    if isinstance(value, datetime.time):
        return "time"
    if isinstance(value, datetime.timedelta):
        return "duration"
    if isinstance(value, _MType):
        return "type"
    if isinstance(value, dict):
        return "record"
    if isinstance(value, list):
        return "list"
    # builtins/_type.py::_matches's own test for M's `function` kind: a
    # `_Lambda` isn't `callable()` (it has no `__call__`), so both halves of
    # that check are needed here too - ported rather than imported because
    # `_Lambda` is a private evaluate.py name this module has no other need
    # of.
    if callable(value) or hasattr(value, "params"):
        return "function"
    return "unknown"


_NUMBER_KINDS = frozenset({"number-int", "number-float"})

# kind -> (what it is, what to do about it). Function names cited below are
# verified registered builtins (grep'd in builtins/_table_join.py,
# _table_shape.py, _record.py before writing this) - never invented.
_UNMAPPABLE: dict[str, tuple[str, str]] = {
    "table": (
        "a table that has not been read",
        'select the one you want first, for example Source{[Schema="dbo", '
        'Item="Orders"]}[Data]',
    ),
    "record": (
        "an M record",
        "expand it first (Table.ExpandRecordColumn) or pick one field (Record.Field)",
    ),
    "list": (
        "an M list or nested table",
        "expand it first (Table.ExpandListColumn or Table.ExpandTableColumn)",
    ),
    "type": (
        "an M type value",
        "a type value describes a shape, not data - there is no "
        "pandas/Arrow column type for it",
    ),
    "function": (
        "an M function value",
        "a function has no data representation in pandas/Arrow - call it "
        "and export the result instead",
    ),
    "unknown": (
        "a value this evaluator's data model does not name",
        "this is a pqtools gap, not a usage error - please file an issue",
    ),
}


def _refuse(column: str, kind: str) -> NoReturn:
    noun, guidance = _UNMAPPABLE[kind]
    raise ExportRefusal(
        f"column {column!r} holds {noun}; pandas/Arrow have no column type "
        f"for it. {guidance}."
    )


def _type_name_for_message(value: Any) -> str:
    """What to call `value` in a refusal - its M kind when we have one."""
    kind = _cell_kind(value)
    return "an M " + kind if kind != "unknown" else f"a {type(value).__name__}"


def _column_order(rows: list[dict[str, Any]]) -> list[str]:
    """Column names in row-0 order, after refusing any ragged row.

    Both pandas and pyarrow fill a missing key with a silent null when rows
    disagree on their columns (verified empirically against pandas 3.0.5 /
    pyarrow 25.0.1 2026-09-06) - exactly the "fill with NaN" PRD s4 refuses.
    Checked once here, ahead of both builders, rather than twice.
    """
    # Shape first. `to_pandas(report.eval("Sales"))` is the README's own
    # example, and a query returning a scalar, a record or a list of scalars
    # is an ordinary thing to have written - it must produce the typed
    # refusal this module documents, not `'int' object is not subscriptable`
    # from somewhere inside the column builder. `cli.py`'s `_export_result`
    # already guarded this; the library entry points did not.
    if not isinstance(rows, list):
        raise ExportRefusal(
            f"export requires the result to be a table (a list of records), "
            f"got {_type_name_for_message(rows)}"
        )
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExportRefusal(
                f"export requires the result to be a table (a list of "
                f"records); row {index} is {_type_name_for_message(row)}"
            )
    if not rows:
        return []
    columns = list(rows[0].keys())
    expected = set(columns)
    for index, row in enumerate(rows):
        if set(row.keys()) != expected:
            raise ExportRefusal(
                f"row {index} has different columns than row 0 "
                f"({sorted(row.keys())} vs {sorted(columns)}); ragged rows "
                "are refused, not filled with a null"
            )
    return columns


def _classify_column(name: str, values: list[Any]) -> str:
    """The single M kind shared by every non-null value in one column.

    Returns one of: null, logical, number, text, binary, date, time,
    datetime, datetimezone, duration. Refuses (never guesses) an unmappable
    kind, a column that mixes M types, or a datetimezone column whose
    values disagree on their UTC offset (Arrow's/pandas' timestamp-with-tz
    dtype carries exactly one offset for the whole column - verified
    2026-09-06 that unforced construction otherwise normalises every row to
    one offset silently).
    """
    kinds = {_cell_kind(value) for value in values if value is not None}
    if not kinds:
        return "null"
    unmappable = kinds & _UNMAPPABLE.keys()
    if unmappable:
        _refuse(name, next(iter(unmappable)))
    if kinds <= _NUMBER_KINDS:
        kind = "number"
    elif len(kinds) > 1:
        raise ExportRefusal(
            f"column {name!r} mixes M types {sorted(kinds)}; pqtools "
            "exports one M type per column, never a coerced mix"
        )
    else:
        kind = next(iter(kinds))
    if kind == "datetimezone":
        offsets = {value.utcoffset() for value in values if value is not None}
        if len(offsets) > 1:
            raise ExportRefusal(
                f"column {name!r} holds datetimezone values with different "
                "UTC offsets; exporting would force one offset onto the "
                "whole column. Normalize with DateTimeZone.SwitchZone first."
            )
    return kind


# --------------------------------------------------------------------------
# pandas
# --------------------------------------------------------------------------


def to_pandas(rows: list[dict[str, Any]]) -> Any:
    """A :class:`pandas.DataFrame` for `rows` - what ``evaluate()`` returns
    for a table. Requires the ``pqtools[pandas]`` extra.
    """
    pd = _require_pandas()
    columns = _column_order(rows)
    data: dict[str, Any] = {}
    for name in columns:
        values = [row[name] for row in rows]
        kind = _classify_column(name, values)
        data[name] = _pandas_column(pd, name, kind, values)
    return pd.DataFrame(data)


def _pandas_column(pd: Any, name: str, kind: str, values: list[Any]) -> Any:
    if kind == "null":
        return pd.Series(values, dtype=object)
    if kind == "logical":
        return pd.array(values, dtype="boolean")
    if kind == "number":
        return _pandas_number_column(pd, name, values)
    if kind in ("text", "binary", "date", "time"):
        # pandas has no first-class dtype for any of these four - `object`
        # keeps the exact Python value (str/bytes/date/time) and keeps a
        # missing cell as literal `None`. Forced explicitly rather than
        # left to inference: pandas 3.0's own default for a plain list of
        # `str` is its new "str" extension dtype, which represents a
        # missing cell as float `nan` - silently conflating "this M value
        # is null" with a different sentinel. Verified against pandas
        # 3.0.5 2026-09-06; `date`/`time`/`binary` were already `object`
        # under plain inference, so this only changes behaviour for text.
        return pd.Series(values, dtype=object)
    # duration / datetime / datetimezone. pandas 3.0's inference from a
    # plain list lands on microsecond resolution, which is what M carries and
    # what SUPPORT-MATRIX.md documents - but pandas 2.x infers NANOsecond
    # (`datetime64[ns]`), and the extras permit `pandas>=2`. Rather than
    # raise the floor to the version this was verified on, ask for the unit
    # explicitly so both agree. A tz-aware column keeps its offset;
    # `_classify_column` has already refused a column that mixes offsets,
    # which is the case a single dtype cannot represent.
    if kind == "duration":
        return pd.Series(values).astype("timedelta64[us]")
    if kind == "datetime":
        return pd.Series(values).astype("datetime64[us]")
    # datetimezone. `_classify_column` admits this column on equal
    # `utcoffset()`, NOT equal `tzinfo`, so rows may legitimately mix
    # `timezone.utc` with `ZoneInfo("Europe/London")` in January. Reading the
    # offset off the first row's `tzinfo` and handing pandas a plain Series
    # therefore broke on exactly that column: pandas infers `object` for
    # mixed tzinfo and `.dt` raises "Can only use .dt accessor with
    # datetimelike values" - a bare traceback where this module promises a
    # refusal, while `to_arrow` on the same rows succeeded because it derives
    # the offset from `utcoffset()`. Mirror Arrow: normalise through UTC,
    # then convert to the one offset the column shares.
    offset = datetime.timezone(
        next(value.utcoffset() for value in values if value is not None)
    )
    converted = pd.to_datetime(values, utc=True).tz_convert(offset)
    return pd.Series(converted).astype(f"datetime64[us, {offset}]")


def _pandas_number_column(pd: Any, name: str, values: list[Any]) -> Any:
    ints_only = all(
        value is None or (isinstance(value, int) and not isinstance(value, bool))
        for value in values
    )
    if ints_only:
        try:
            return pd.array(values, dtype="Int64")
        except OverflowError as error:
            raise ExportRefusal(
                f"column {name!r}: a value exceeds the 64-bit integer range "
                "pandas' nullable Int64 can hold"
            ) from error
    has_null = any(value is None for value in values)
    has_nan = any(isinstance(value, float) and math.isnan(value) for value in values)
    if has_null and has_nan:
        raise ExportRefusal(
            f"column {name!r} holds both null and the M number #nan; "
            "pandas float64 represents both as NaN and cannot tell them "
            "apart after export"
        )
    return pd.Series(values, dtype="float64")


# --------------------------------------------------------------------------
# Arrow / parquet
# --------------------------------------------------------------------------


def _offset_string(offset: datetime.timedelta) -> str:
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def to_arrow(rows: list[dict[str, Any]]) -> Any:
    """A :class:`pyarrow.Table` for `rows`. Requires ``pqtools[arrow]``."""
    pa = _require_pyarrow()
    columns = _column_order(rows)
    arrays: dict[str, Any] = {}
    for name in columns:
        values = [row[name] for row in rows]
        kind = _classify_column(name, values)
        arrays[name] = _arrow_array(pa, name, kind, values)
    return pa.table(arrays)


def _arrow_array(pa: Any, name: str, kind: str, values: list[Any]) -> Any:
    if kind == "null":
        return pa.array(values, type=pa.null())
    if kind == "logical":
        return pa.array(values, type=pa.bool_())
    if kind == "number":
        return _arrow_number_array(pa, name, values)
    if kind == "text":
        return pa.array(values, type=pa.string())
    if kind == "binary":
        return pa.array(values, type=pa.binary())
    if kind == "date":
        return pa.array(values, type=pa.date32())
    if kind == "time":
        return pa.array(values, type=pa.time64("us"))
    if kind == "duration":
        return pa.array(values, type=pa.duration("us"))
    if kind == "datetime":
        return pa.array(values, type=pa.timestamp("us"))
    # datetimezone: _classify_column already refused a mixed-offset column,
    # so every non-null value shares one offset - built explicitly rather
    # than left to pa.array's own inference, which normalises a mixed
    # column onto its first row's offset instead of refusing (verified
    # against pyarrow 25.0.1 2026-09-06).
    offset = next(value.utcoffset() for value in values if value is not None)
    return pa.array(values, type=pa.timestamp("us", tz=_offset_string(offset)))


def _arrow_number_array(pa: Any, name: str, values: list[Any]) -> Any:
    ints_only = all(
        value is None or (isinstance(value, int) and not isinstance(value, bool))
        for value in values
    )
    arrow_type = pa.int64() if ints_only else pa.float64()
    try:
        return pa.array(values, type=arrow_type)
    except OverflowError as error:
        raise ExportRefusal(
            f"column {name!r}: a value exceeds the 64-bit integer range "
            "Arrow's int64 can hold"
        ) from error


def to_parquet(rows: list[dict[str, Any]], path: str | Path) -> None:
    """Write `rows` to a parquet file at `path`. Requires ``pqtools[arrow]``."""
    table = to_arrow(rows)  # raises ExportRefusal if pyarrow itself is missing
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as error:
        raise ExportRefusal(
            "pyarrow is installed but its parquet module is not available. "
            f"Install it with: {_install_hint('arrow')}"
        ) from error
    pq.write_table(table, Path(path))


# --------------------------------------------------------------------------
# pqtools.open() - a discoverability handle, not a transform layer
# --------------------------------------------------------------------------


_member_expression = containers.member_expression


def _discover(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(named ``shared`` members, anonymous single-query sections) for `path`.

    The same container/plain-file split the CLI makes, through the same
    `containers.is_container` - containers and directories go through
    ``read_sections``, everything else is read as raw text.
    """
    members: dict[str, str] = {}
    anonymous: dict[str, str] = {}
    if containers.is_container(path):
        for section in read_sections(path):
            found = split_shared(section.source, section.container)
            if found:
                for member_name, text in found.items():
                    if member_name in members:
                        raise MQueryError(
                            f"{path}: shared member {member_name!r} is "
                            "defined in more than one section"
                        )
                    members[member_name] = text
            else:
                # A section with no `shared` member is a bare expression -
                # the file's own stem is the only name it has.
                stem = Path(section.path).stem
                if stem in anonymous or stem in members:
                    raise MQueryError(
                        f"{path}: cannot form a unique query name for {section.path!r}"
                    )
                anonymous[stem] = section.source
    else:
        text = _snapshot(path).data.decode("utf-8", "strict")
        found = split_shared(text, str(path))
        if found:
            members.update(found)
        else:
            anonymous[path.stem] = text
    return members, anonymous


class PqFile:
    """A read-only discoverability handle over one Power Query file.

    ``.queries``, ``.source(name)``, ``.eval(name)`` - no ``.filter()``, no
    ``.groupby()``. Transforms belong in M, where they fold; see
    ``.planning/PRD-pandas-for-powerquery-2026-09-06.md`` s3 for why this
    stays a handle and never grows into a second, silently different
    DataFrame-like dialect.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._members, self._anonymous = _discover(path)

    @property
    def queries(self) -> list[str]:
        return [*self._members, *self._anonymous]

    def source(self, name: str) -> str:
        """`name`'s M source, unevaluated - the expression, not the statement.

        The same text `pq show --member NAME` prints. An earlier cut returned
        the whole ``shared NAME = <expr>;`` statement here while the CLI
        returned ``<expr>``, so the two surfaces disagreed about what a
        query's source is; one answer is worth more than either.
        """
        if name in self._members:
            return containers.member_expression(self._members[name])
        if name in self._anonymous:
            return self._anonymous[name]
        raise MQueryError(f"{self._path}: no query named {name!r}")

    def eval(
        self,
        name: str,
        *,
        bindings: dict[str, Any] | None = None,
        io: IOPolicy = DENY_ALL,
    ) -> Any:
        """Run `name` and return its value, via the same ``evaluate()``
        every other entry point uses.

        `bindings` and `io` are forwarded unchanged, so this handle can run
        the connector-backed queries that are the ordinary case - the
        equivalents of ``pq eval --bind`` and ``--allow-net``. Without them
        the facade was pinned to `DENY_ALL` and could only ever run a query
        that reads nothing, which is not the query anyone opens a `.pbix` to
        find. The default stays `DENY_ALL`: reaching the network is the
        caller's decision to make explicitly, here as on the command line.
        """
        return evaluate(self.source(name), bindings=bindings, io=io)


def open(path: str | Path) -> PqFile:
    """A discoverability handle over the queries in `path` - see :class:`PqFile`."""
    return PqFile(Path(path))
