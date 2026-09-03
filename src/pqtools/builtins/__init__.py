"""Assembles the M builtin-function registry from the per-family modules.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md's "Architecture change"
section. Each family module owns one M namespace (``Table.*``, ``Text.*``,
``List.*``, ``Record.*``, ``Number.*``/``Logical.*``/``Json.*``); this
module just wires them into one dict keyed by the M-visible name, exactly
as the single ``BUILTINS`` dict at the bottom of ``evaluate.py`` did before
the split.

``evaluate.py`` imports ``BUILTINS`` from here (``from .builtins import
BUILTINS``) and re-exports it, so ``pqtools.evaluate.BUILTINS`` - documented
in README.md as the source of truth for the supported-builtins list -
keeps working unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import _list, _number, _record, _table, _text

if TYPE_CHECKING:
    from ..evaluate import _Ctx

_Builtin = Callable[[list[Any], "_Ctx"], Any]

BUILTINS: dict[str, _Builtin] = {
    "Text.From": _text._text_from,
    "Text.Upper": _text._text_upper,
    "Text.Lower": _text._text_lower,
    "Text.Length": _text._text_length,
    "Text.Combine": _text._text_combine,
    "Text.Contains": _text._text_contains,
    "Text.Replace": _text._text_replace,
    "Text.Split": _text._text_split,
    "Text.Start": _text._text_start,
    "Text.End": _text._text_end,
    "Text.Trim": _text._text_trim,
    "Number.From": _number._number_from,
    "Number.Round": _number._number_round,
    "Number.Abs": _number._number_abs,
    "List.Count": _list._list_count,
    "List.Sum": _list._list_sum,
    "List.Max": _list._list_max,
    "List.Min": _list._list_min,
    "List.Average": _list._list_average,
    "List.Transform": _list._list_transform,
    "List.Select": _list._list_select,
    "List.First": _list._list_first,
    "List.Last": _list._list_last,
    "List.Reverse": _list._list_reverse,
    "List.Sort": _list._list_sort,
    "List.Contains": _list._list_contains,
    "List.Distinct": _list._list_distinct,
    "List.Range": _list._list_range,
    "Record.Field": _record._record_field_builtin,
    "Record.FieldNames": _record._record_field_names,
    "Record.HasFields": _record._record_has_fields,
    "Record.AddField": _record._record_add_field,
    "Record.RemoveFields": _record._record_remove_fields,
    "Table.FromRecords": _table._table_from_records,
    "Table.ToRecords": _table._table_to_records,
    "Table.RowCount": _table._table_row_count,
    "Table.ColumnNames": _table._table_column_names,
    "Table.SelectRows": _table._table_select_rows,
    "Table.SelectColumns": _table._table_select_columns,
    "Table.RemoveColumns": _table._table_remove_columns,
    "Table.RenameColumns": _table._table_rename_columns,
    "Table.AddColumn": _table._table_add_column,
    "Table.TransformColumns": _table._table_transform_columns,
    "Table.Sort": _table._table_sort,
    "Table.FirstN": _table._table_first_n,
    "Table.LastN": _table._table_last_n,
    "Table.Distinct": _table._table_distinct,
    "Json.Document": _number._json_document,
    "Logical.From": _number._logical_from,
}

__all__ = ["BUILTINS"]
