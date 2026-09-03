"""``Record.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import EvalError, _arity, _field_name_list, _require_record, _require_str

if TYPE_CHECKING:
    from ..evaluate import _Ctx


def _record_field_builtin(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.Field", args, 2)
    record = _require_record(args[0])
    name = _require_str(args[1])
    if name not in record:
        raise EvalError(f"Record.Field: no such field: {name}")
    return record[name]


def _record_field_names(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.FieldNames", args, 1)
    return list(_require_record(args[0]).keys())


def _record_has_fields(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.HasFields", args, 2)
    record = _require_record(args[0])
    return all(name in record for name in _field_name_list(args[1]))


def _record_add_field(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.AddField", args, 3)
    record = _require_record(args[0])
    name = _require_str(args[1])
    if name in record:
        raise EvalError(f"Record.AddField: field already exists: {name}")
    result = dict(record)
    result[name] = args[2]
    return result


def _record_remove_fields(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.RemoveFields", args, 2)
    record = _require_record(args[0])
    names = _field_name_list(args[1])
    for name in names:
        if name not in record:
            raise EvalError(f"Record.RemoveFields: no such field: {name}")
    return {key: value for key, value in record.items() if key not in names}
